from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from db import connect

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])


class NoteBody(BaseModel):
    content: str


class NoteSummary(BaseModel):
    id: str
    title: str
    preview: str
    created_at: str


@router.get("", response_model=list[NoteSummary])
def list_notes(limit: int = Query(10, ge=1, le=100)):
    with connect() as db:
        rows = db.execute(
            "SELECT id, title, substr(content, 1, 120) AS preview, created_at "
            "FROM notes ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        NoteSummary(id=r[0], title=r[1], preview=r[2], created_at=r[3])
        for r in rows
    ]


@router.get("/{uuid}", response_class=PlainTextResponse)
def get_note(uuid: str):
    with connect() as db:
        row = db.execute("SELECT content FROM notes WHERE id = ?", (uuid,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="note not found")
    return row[0]


@router.put("/{uuid}")
def put_note(uuid: str, body: NoteBody):
    with connect() as db:
        db.execute(
            "INSERT INTO notes (id, content) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "content = excluded.content, updated_at = datetime('now')",
            (uuid, body.content),
        )
    return {"id": uuid}
