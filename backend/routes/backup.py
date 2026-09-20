import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from db import manual_backup_path, try_create_manual_backup

router = APIRouter(prefix="/api/v1/backup", tags=["backup"])


@router.post("")
def create_backup():
    if not try_create_manual_backup():
        raise HTTPException(status_code=409, detail="a backup is already in progress")
    path = manual_backup_path()
    return {"filename": os.path.basename(path), "size": os.stat(path).st_size}


@router.get("")
def download_backup():
    path = manual_backup_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="no backup has been created yet")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=os.path.basename(path),
        # A backup is mutable/regenerable — overwritten by the next POST — so
        # never let a client or intermediary cache it.
        headers={"Cache-Control": "no-store"},
    )
