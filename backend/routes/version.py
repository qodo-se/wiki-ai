import pathlib

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["version"])

# Lives beside app.py (not at the repo root) so it ships inside the Docker build
# context — docker-compose builds the api image from ./backend, and Docker can't
# COPY a file from outside that context.
_VERSION = (pathlib.Path(__file__).parent.parent / "VERSION").read_text().strip()


@router.get("/version")
def get_version():
    return {"version": _VERSION}
