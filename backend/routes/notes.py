import sys
import uuid as uuidlib

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from categorize import reorganize_notes
from db import connect
from embeddings import EmbeddingError, embed_text
from vectorstore import VectorStoreError, delete_vector, upsert_vector

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])


def _sync_embedding(note_id: str, content: str) -> None:
    # Semantic search is a best-effort add-on — a down/unconfigured Ollama or
    # Qdrant must never block saving a note.
    try:
        vector = embed_text(content)
        upsert_vector(note_id, vector)
    except (EmbeddingError, VectorStoreError) as e:
        print(f"warning: failed to embed note {note_id}: {e}", file=sys.stderr)


def _delete_embedding(note_id: str) -> None:
    try:
        delete_vector(note_id)
    except VectorStoreError as e:
        print(f"warning: failed to delete vector for note {note_id}: {e}", file=sys.stderr)

PATH_PATTERN = r"^[A-Za-z0-9 _./-]*$"


class NoteBody(BaseModel):
    content: str
    path: str = Field(default="/", max_length=200, pattern=PATH_PATTERN)


class NoteSummary(BaseModel):
    id: str
    title: str
    path: str
    preview: str
    created_at: str


class NoteMeta(BaseModel):
    id: str
    title: str
    path: str
    created_at: str
    updated_at: str


class NoteListResponse(BaseModel):
    items: list[NoteSummary]
    total: int
    limit: int
    offset: int


@router.get("", response_model=NoteListResponse)
def list_notes(limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0)):
    with connect() as db:
        # BEGIN so both reads share one snapshot — otherwise a concurrent
        # create/delete between the two statements can make `total` disagree
        # with `items`, producing an impossible page for the caller.
        db.execute("BEGIN")
        total = db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        rows = db.execute(
            "SELECT id, title, path, substr(content, 1, 120) AS preview, created_at "
            "FROM notes ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    items = [
        NoteSummary(id=r[0], title=r[1], path=r[2], preview=r[3], created_at=r[4])
        for r in rows
    ]
    return NoteListResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("/reorganize")
def reorganize():
    return reorganize_notes()


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
            "SELECT id, title, path, created_at, updated_at FROM notes WHERE id = ?",
            (uuid,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="note not found")
    return NoteMeta(id=row[0], title=row[1], path=row[2], created_at=row[3], updated_at=row[4])


@router.post("")
def create_note(body: NoteBody):
    new_id = str(uuidlib.uuid4())
    with connect() as db:
        db.execute(
            "INSERT INTO notes (id, content, path) VALUES (?, ?, ?)",
            (new_id, body.content, body.path),
        )
    _sync_embedding(new_id, body.content)
    return {"id": new_id}


@router.put("/{uuid}")
def put_note(uuid: str, body: NoteBody):
    with connect() as db:
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
        cursor = db.execute("DELETE FROM notes WHERE id = ?", (uuid,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="note not found")
    _delete_embedding(uuid)
