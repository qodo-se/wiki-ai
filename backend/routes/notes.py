import uuid as uuidlib

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from db import connect

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])

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


@router.get("", response_model=list[NoteSummary])
def list_notes(limit: int = Query(10, ge=1, le=100)):
    with connect() as db:
        rows = db.execute(
            "SELECT id, title, path, substr(content, 1, 120) AS preview, created_at "
            "FROM notes ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        NoteSummary(id=r[0], title=r[1], path=r[2], preview=r[3], created_at=r[4])
        for r in rows
    ]


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
    return {"id": uuid}


@router.delete("/{uuid}", status_code=204)
def delete_note(uuid: str):
    with connect() as db:
        cursor = db.execute("DELETE FROM notes WHERE id = ?", (uuid,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="note not found")
