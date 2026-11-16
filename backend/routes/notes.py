import sqlite3
import sys
import uuid as uuidlib

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from categorize import ReorganizeInProgress, reorganize_notes
from chunking import chunk_content, chunk_point_id
from db import connect
from embeddings import EmbeddingError, embed_text
from paths import normalize_path
from routes.images import delete_note_images
from search import _derive_title
from tree import (
    KIND_CONTAINER,
    InvalidParent,
    NoteNotFound,
    Scope,
    ancestors as tree_ancestors,
    children as tree_children,
    promote_children,
    scope_sql,
    set_parent,
)
from vectorstore import VectorStoreError, delete_stale_chunks, delete_vectors_for_note, upsert_vectors

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])


def _sync_embedding(note_id: str, content: str) -> None:
    # Semantic search is a best-effort add-on — a down/unconfigured Ollama or
    # Qdrant must never block saving a note.
    try:
        # Embed everything, then upsert the new chunks in place at their
        # deterministic point ids (overwriting same-index chunks from the
        # note's previous version), and only then delete any leftover
        # chunk-points beyond the new count (from a note that shrank). This
        # ordering means a failure at any step never leaves fewer working
        # vectors than before the save: an embedding failure touches nothing,
        # and an upsert failure on one chunk leaves that chunk's previous
        # vector untouched rather than deleted-with-nothing-to-replace-it.
        chunks = chunk_content(content)
        vectors = [embed_text(chunk) for chunk in chunks]
        upsert_vectors(
            [
                (chunk_point_id(note_id, i), vector, {"note_id": note_id, "chunk_index": i})
                for i, vector in enumerate(vectors)
            ]
        )
        delete_stale_chunks(note_id, keep_count=len(vectors))
    except (EmbeddingError, VectorStoreError) as e:
        print(f"warning: failed to embed note {note_id}: {e}", file=sys.stderr)


def _delete_embedding(note_id: str) -> None:
    try:
        delete_vectors_for_note(note_id)
    except VectorStoreError as e:
        print(f"warning: failed to delete vectors for note {note_id}: {e}", file=sys.stderr)


def _delete_images(note_id: str) -> None:
    # Best-effort, like _delete_embedding above — the note itself is already
    # gone by the time this runs, so a failure here (disk I/O, or a locked
    # second connection to the DB) must not turn into a 500 for a delete that
    # already succeeded.
    try:
        delete_note_images(note_id)
    except (OSError, sqlite3.Error) as e:
        print(f"warning: failed to delete images for note {note_id}: {e}", file=sys.stderr)

PATH_PATTERN = r"^[A-Za-z0-9 _./-]*$"


def _has_title_line(content: str) -> bool:
    # Asks the actual title deriver rather than approximating it with "has a
    # non-blank line". Content of just "#" clears that lower bar but derives
    # to "" once the heading marker is stripped, which would save exactly the
    # unnameable container this check exists to reject.
    from search import _derive_title

    return bool(_derive_title(content).strip())


class NoteBody(BaseModel):
    content: str
    path: str = Field(default="/", max_length=200, pattern=PATH_PATTERN)

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, v):
        return normalize_path(v) if isinstance(v, str) else v


class NoteCreate(NoteBody):
    # Only create takes `kind`. Converting an existing note into a container
    # (or back) is a different operation with different consequences — it
    # would move the note in and out of every search result — so PUT
    # deliberately can't do it by accident.
    kind: Literal["note", "container"] = "note"

    @model_validator(mode="after")
    def _container_needs_a_title(self):
        if self.kind == KIND_CONTAINER and not _has_title_line(self.content):
            raise ValueError(
                "a container needs a title line — its first non-blank line "
                "becomes its name in the tree"
            )
        return self


class NoteSummary(BaseModel):
    id: str
    title: str
    path: str
    preview: str
    created_at: str
    # Carrying parent_id on every list item is what makes the whole tree
    # skeleton fetchable in one request (list with scope=containers) instead
    # of needing a recursive walk down from some root.
    kind: str
    parent_id: str | None


class NoteMeta(BaseModel):
    id: str
    title: str
    path: str
    created_at: str
    updated_at: str
    kind: str
    parent_id: str | None


class NoteListResponse(BaseModel):
    items: list[NoteSummary]
    total: int
    limit: int
    offset: int


class ParentBody(BaseModel):
    parent_id: str | None = None


class AncestorNode(BaseModel):
    id: str
    title: str
    kind: str
    content: str
    content_truncated: bool


class AncestorsResponse(BaseModel):
    # Unpaginated on purpose: the chain is bounded by tree.MAX_DEPTH, and a
    # caller assembling context wants all of it or none of it.
    items: list[AncestorNode]


class ChildSummary(BaseModel):
    id: str
    title: str
    kind: str
    preview: str
    created_at: str


class ChildListResponse(BaseModel):
    items: list[ChildSummary]
    total: int
    limit: int
    offset: int


@router.get("", response_model=NoteListResponse)
def list_notes(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    scope: Scope = Query(Scope.notes, description="which kinds of note to return"),
):
    # The predicate applies to the COUNT as well as the page: a `total` that
    # counts containers while `items` excludes them is the same leak, just
    # harder to notice.
    scope_predicate, scope_params = scope_sql(scope)
    with connect() as db:
        # BEGIN so both reads share one snapshot — otherwise a concurrent
        # create/delete between the two statements can make `total` disagree
        # with `items`, producing an impossible page for the caller.
        db.execute("BEGIN")
        total = db.execute(
            f"SELECT COUNT(*) FROM notes WHERE {scope_predicate}", scope_params
        ).fetchone()[0]
        rows = db.execute(
            "SELECT id, title, path, substr(content, 1, 120) AS preview, created_at, "
            # The same leading slice feeds both the preview and the derived
            # title below -- a title comes from the first non-blank line, so
            # 120 characters is always enough and whole contents needn't be
            # read for a listing.
            "kind, parent_id, substr(content, 1, 120) "
            f"FROM notes WHERE {scope_predicate} "
            "ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (*scope_params, limit, offset),
        ).fetchall()
    # Falls back to the derived title exactly as search.py does. notes.title
    # is never written by anything, so without this every list item -- and so
    # every container in a scope=containers skeleton -- comes back nameless,
    # while the same row has a title through search, children and ancestors.
    items = [
        NoteSummary(
            id=r[0], title=r[1] or _derive_title(r[7]), path=r[2], preview=r[3],
            created_at=r[4], kind=r[5], parent_id=r[6],
        )
        for r in rows
    ]
    return NoteListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{uuid}", response_class=PlainTextResponse)
def get_note(uuid: str):
    with connect() as db:
        row = db.execute("SELECT content FROM notes WHERE id = ?", (uuid,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="note not found")
    return row[0]


@router.get("/{uuid}/meta", response_model=NoteMeta)
def get_note_meta(uuid: str):
    with connect() as db:
        row = db.execute(
            "SELECT id, title, path, created_at, updated_at, kind, parent_id "
            "FROM notes WHERE id = ?",
            (uuid,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="note not found")
    return NoteMeta(
        id=row[0], title=row[1], path=row[2], created_at=row[3], updated_at=row[4],
        kind=row[5], parent_id=row[6],
    )


@router.post("")
def create_note(body: NoteCreate):
    new_id = str(uuidlib.uuid4())
    with connect() as db:
        db.execute(
            "INSERT INTO notes (id, content, path, kind) VALUES (?, ?, ?, ?)",
            (new_id, body.content, body.path, body.kind),
        )
    _sync_embedding(new_id, body.content)
    return {"id": new_id}


@router.put("/{uuid}")
def put_note(uuid: str, body: NoteBody):
    with connect() as db:
        existing = db.execute("SELECT kind FROM notes WHERE id = ?", (uuid,)).fetchone()
        if existing is not None and existing[0] == KIND_CONTAINER and not _has_title_line(body.content):
            raise HTTPException(
                status_code=422,
                detail=(
                    "a container needs a title line — its first non-blank line "
                    "becomes its name in the tree"
                ),
            )
        cursor = db.execute(
            "UPDATE notes SET content = ?, path = ?, updated_at = datetime('now') WHERE id = ?",
            (body.content, body.path, uuid),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="note not found")
    _sync_embedding(uuid, body.content)
    return {"id": uuid}


@router.delete("/{uuid}", status_code=204)
def delete_note(uuid: str):
    with connect() as db:
        # Same reason set_parent uses it: without the write lock held from the
        # start, a concurrent move of this container between the read below
        # and the promotion would re-home its children onto the parent it used
        # to have rather than the one it had when it was deleted.
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT parent_id FROM notes WHERE id = ?", (uuid,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="note not found")
        # Anything filed under this note moves up to where this note itself
        # lived, in the same transaction as the delete — a crash between the
        # two would otherwise leave children pointing at a row that's gone.
        promote_children(db, uuid, row[0])
        db.execute("DELETE FROM notes WHERE id = ?", (uuid,))
    _delete_embedding(uuid)
    _delete_images(uuid)


@router.put("/{uuid}/parent")
def put_note_parent(uuid: str, body: ParentBody):
    """Set or clear a note's primary container."""
    try:
        set_parent(uuid, body.parent_id)
    except NoteNotFound:
        raise HTTPException(status_code=404, detail="note not found")
    except InvalidParent as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": uuid, "parent_id": body.parent_id}


@router.get("/{uuid}/ancestors", response_model=AncestorsResponse)
def get_note_ancestors(uuid: str):
    """The chain of containers this note lives under, root first."""
    try:
        items = tree_ancestors(uuid)
    except NoteNotFound:
        raise HTTPException(status_code=404, detail="note not found")
    return AncestorsResponse(items=[AncestorNode(**a) for a in items])


@router.get("/{uuid}/children", response_model=ChildListResponse)
def get_note_children(
    uuid: str,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """One level down: what's filed directly under this note."""
    try:
        items, total = tree_children(uuid, limit, offset)
    except NoteNotFound:
        raise HTTPException(status_code=404, detail="note not found")
    return ChildListResponse(
        items=[ChildSummary(**c) for c in items], total=total, limit=limit, offset=offset
    )


@router.post("/reorganize")
def reorganize():
    try:
        return reorganize_notes()
    except ReorganizeInProgress:
        raise HTTPException(status_code=409, detail="a reorganize run is already in progress")
