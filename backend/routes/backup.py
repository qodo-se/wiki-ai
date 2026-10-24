import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from db import (
    current_manual_backup_snapshot_id,
    manual_backup_path,
    try_create_manual_backup,
)

router = APIRouter(prefix="/api/v1/backup", tags=["backup"])


@router.post("")
def create_backup():
    snapshot_id = try_create_manual_backup()
    if snapshot_id is None:
        raise HTTPException(status_code=409, detail="a backup is already in progress")
    path = manual_backup_path()
    return {
        "filename": os.path.basename(path),
        "size": os.stat(path).st_size,
        "snapshot_id": snapshot_id,
    }


@router.get("")
def download_backup(snapshot_id: str | None = None):
    path = manual_backup_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="no backup has been created yet")
    if snapshot_id is not None and snapshot_id != current_manual_backup_snapshot_id():
        # A newer trigger (another caller, a future scheduled job) has
        # overwritten the file since the caller's own trigger — serving it
        # anyway would silently hand them a different snapshot than the one
        # they asked for. A caller that omits snapshot_id entirely gets
        # today's unchanged behavior: whatever's currently on disk.
        raise HTTPException(
            status_code=409,
            detail="this backup has been superseded by a newer one — trigger a new backup and download that one instead",
        )
    return FileResponse(
        path,
        media_type="application/zip",
        filename=os.path.basename(path),
        # A backup is mutable/regenerable — overwritten by the next POST — so
        # never let a client or intermediary cache it.
        headers={"Cache-Control": "no-store"},
    )
