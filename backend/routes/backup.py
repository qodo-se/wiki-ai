import os

from fastapi import APIRouter, HTTPException, Response

from db import (
    ManualBackupNotFound,
    ManualBackupSuperseded,
    manual_backup_path,
    read_manual_backup,
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
    try:
        data = read_manual_backup(snapshot_id)
    except ManualBackupNotFound:
        raise HTTPException(status_code=404, detail="no backup has been created yet")
    except ManualBackupSuperseded:
        # A newer trigger (another caller, a future scheduled job) has
        # overwritten the file since the caller's own trigger — serving it
        # anyway would silently hand them a different snapshot than the one
        # they asked for. A caller that omits snapshot_id entirely gets
        # today's unchanged behavior: whatever's currently on disk.
        raise HTTPException(
            status_code=409,
            detail="this backup has been superseded by a newer one — trigger a new backup and download that one instead",
        )
    # A plain Response over already-read bytes, not FileResponse(path): the
    # id check above and this response both happen while read_manual_backup
    # holds the backup lock, so by the time we get here the bytes already
    # match the validated id — a lazily-streamed FileResponse would instead
    # reopen the (possibly since-replaced) path after the lock was released.
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{os.path.basename(manual_backup_path())}"',
            # A backup is mutable/regenerable — overwritten by the next POST —
            # so never let a client or intermediary cache it.
            "Cache-Control": "no-store",
        },
    )
