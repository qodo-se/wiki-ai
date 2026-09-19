import uuid as uuidlib

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import Response

from db import connect

router = APIRouter(prefix="/api/v1/images", tags=["images"])

# SVG is deliberately excluded — it would be served same-origin, and an SVG can carry
# a <script> that runs with this app's own origin.
ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


@router.post("")
async def create_image(file: UploadFile, note_id: str = Form(...)):
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=415, detail="unsupported image type")

    data = await file.read()

    new_id = str(uuidlib.uuid4())
    with connect() as db:
        row = db.execute("SELECT 1 FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="note not found")
        db.execute(
            "INSERT INTO images (id, note_id, filename, mime_type, size, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_id, note_id, file.filename or "", file.content_type, len(data), data),
        )
    return {"id": new_id, "url": f"/api/v1/images/{new_id}"}


@router.get("/{uuid}")
def get_image(uuid: str):
    with connect() as db:
        row = db.execute(
            "SELECT mime_type, data FROM images WHERE id = ?", (uuid,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="image not found")
    mime_type, data = row
    return Response(
        content=data,
        media_type=mime_type,
        # An image's bytes never change after upload (no update endpoint), so this
        # response is safe to cache forever under its id.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.delete("/{uuid}", status_code=204)
def delete_image(uuid: str):
    with connect() as db:
        cursor = db.execute("DELETE FROM images WHERE id = ?", (uuid,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="image not found")
