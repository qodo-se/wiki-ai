import os
import uuid as uuidlib

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

from db import connect, images_dir

router = APIRouter(prefix="/api/v1", tags=["images"])


def _stored_path(image_id: str, filename: str) -> str:
    # Keeps the original extension on disk (and so in the backup zip too,
    # since that just archives whatever's in images_dir()) — os.path.basename
    # first so a filename smuggling "/" can't escape images_dir() via the
    # extension.
    ext = os.path.splitext(os.path.basename(filename))[1]
    return os.path.join(images_dir(), image_id + ext)


@router.post("/notes/{note_id}/images")
async def upload_image(note_id: str, file: UploadFile):
    with connect() as db:
        note = db.execute("SELECT id FROM notes WHERE id = ?", (note_id,)).fetchone()
        if note is None:
            raise HTTPException(status_code=404, detail="note not found")

        image_id = str(uuidlib.uuid4())
        filename = file.filename or image_id
        data = await file.read()
        os.makedirs(images_dir(), exist_ok=True)
        with open(_stored_path(image_id, filename), "wb") as f:
            f.write(data)

        db.execute(
            "INSERT INTO images (id, note_id, filename, mime_type, size) VALUES (?, ?, ?, ?, ?)",
            (
                image_id,
                note_id,
                filename,
                # A guessed/browser-supplied content-type is only ever a label
                # for how to serve the file back, never a gate on accepting it.
                file.content_type or "application/octet-stream",
                len(data),
            ),
        )
    return {"id": image_id, "url": f"/api/v1/images/{image_id}"}


@router.get("/images/{image_id}")
def get_image(image_id: str):
    with connect() as db:
        row = db.execute(
            "SELECT filename, mime_type FROM images WHERE id = ?", (image_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="image not found")
    filename, mime_type = row
    path = _stored_path(image_id, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="image not found")
    # inline, not the FileResponse default of attachment — this URL is embedded
    # directly as a markdown image (![alt](url)), and an attachment disposition
    # makes a browser try to download it instead of displaying it when the URL
    # is opened directly (e.g. "open image in new tab").
    return FileResponse(path, media_type=mime_type, filename=filename, content_disposition_type="inline")


@router.delete("/images/{image_id}", status_code=204)
def delete_image(image_id: str):
    with connect() as db:
        row = db.execute("SELECT filename FROM images WHERE id = ?", (image_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="image not found")
        # Removes the file before the row, so a failed/permission-denied removal
        # leaves the row intact and the image still reachable, rather than
        # deleting the row and stranding an unreachable file on disk.
        path = _stored_path(image_id, row[0])
        if os.path.exists(path):
            os.remove(path)
        db.execute("DELETE FROM images WHERE id = ?", (image_id,))


def delete_note_images(note_id: str) -> None:
    """Deletes every image belonging to a note. Called from notes.delete_note
    as its cascade-delete step."""
    with connect() as db:
        rows = db.execute("SELECT id, filename FROM images WHERE note_id = ?", (note_id,)).fetchall()
        db.execute("DELETE FROM images WHERE note_id = ?", (note_id,))
    for image_id, filename in rows:
        path = _stored_path(image_id, filename)
        if os.path.exists(path):
            os.remove(path)
