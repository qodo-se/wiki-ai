import os
import uuid as uuidlib

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from db import connect, image_path, MIME_TO_EXT

router = APIRouter(prefix="/api/v1/images", tags=["images"])

# SVG is deliberately excluded — it would be served same-origin, and an SVG can carry
# a <script> that runs with this app's own origin.
ALLOWED_MIME_TYPES = set(MIME_TO_EXT)


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
        dest = image_path(new_id, file.content_type)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        db.execute(
            "INSERT INTO images (id, note_id, filename, mime_type, size) "
            "VALUES (?, ?, ?, ?, ?)",
            (new_id, note_id, file.filename or "", file.content_type, len(data)),
        )
    return {"id": new_id, "url": f"/api/v1/images/{new_id}"}


@router.get("/{uuid}")
def get_image(uuid: str):
    with connect() as db:
        row = db.execute("SELECT mime_type FROM images WHERE id = ?", (uuid,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="image not found")
    mime_type = row[0]
    path = image_path(uuid, mime_type)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(
        path,
        media_type=mime_type,
        # An image's bytes never change after upload (no update endpoint), so this
        # response is safe to cache forever under its id.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.delete("/{uuid}", status_code=204)
def delete_image(uuid: str):
    with connect() as db:
        row = db.execute("SELECT mime_type FROM images WHERE id = ?", (uuid,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="image not found")
        db.execute("DELETE FROM images WHERE id = ?", (uuid,))
    path = image_path(uuid, row[0])
    if os.path.exists(path):
        os.remove(path)
