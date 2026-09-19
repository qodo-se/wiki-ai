import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from db import create_backup_now, open_latest_backup

router = APIRouter(prefix="/api/v1/backup", tags=["backup"])


@router.post("")
def create_backup():
    try:
        path = create_backup_now()
    except RuntimeError:
        raise HTTPException(status_code=429, detail="backup already in progress")
    return {"filename": os.path.basename(path), "size": os.stat(path).st_size}


@router.get("/latest")
def download_latest_backup():
    path, handle = open_latest_backup()
    if path is None:
        raise HTTPException(status_code=404, detail="no backup has been created yet")
    return StreamingResponse(
        handle,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{os.path.basename(path)}"', "Cache-Control": "no-store"},
    )
