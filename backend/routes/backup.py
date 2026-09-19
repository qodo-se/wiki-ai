import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from db import create_backup_now, latest_backup_path

router = APIRouter(prefix="/api/v1/backup", tags=["backup"])


@router.post("")
def create_backup():
    path = create_backup_now()
    return {"filename": os.path.basename(path), "size": os.stat(path).st_size}


@router.get("/latest")
def download_latest_backup():
    path = latest_backup_path()
    if path is None:
        raise HTTPException(status_code=404, detail="no backup has been created yet")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=os.path.basename(path),
        # Backups are mutable/regenerable snapshots, not immutable content like
        # images — never let a client or intermediary cache "the latest" one.
        headers={"Cache-Control": "no-store"},
    )
