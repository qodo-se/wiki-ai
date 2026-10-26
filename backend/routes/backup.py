import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from db import (
    ManualBackupNotFound,
    ManualBackupSuperseded,
    manual_backup_path,
    open_manual_backup,
    try_create_manual_backup,
)

router = APIRouter(prefix="/api/v1/backup", tags=["backup"])

_CHUNK_SIZE = 64 * 1024


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


def _stream_and_close(f):
    try:
        while chunk := f.read(_CHUNK_SIZE):
            yield chunk
    finally:
        f.close()


@router.get("")
def download_backup(snapshot_id: str | None = None):
    try:
        f = open_manual_backup(snapshot_id)
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
    # Streamed from the handle open_manual_backup() already validated and
    # opened under the backup lock — not FileResponse(path), which would
    # reopen the (possibly since-replaced) path after the lock was released,
    # and not a single read() into memory, which would hold the whole ZIP in
    # process memory for the length of the response.
    return StreamingResponse(
        _stream_and_close(f),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{os.path.basename(manual_backup_path())}"',
            # A backup is mutable/regenerable — overwritten by the next POST —
            # so never let a client or intermediary cache it.
            "Cache-Control": "no-store",
        },
    )
