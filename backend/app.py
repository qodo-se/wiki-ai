import pathlib

from fastapi import FastAPI
from fastapi.responses import FileResponse

import db
from routes import config, notes, search, version

STATIC_DIR = pathlib.Path("/app/static")

# Runs schema migrations (and the downgrade guard) at process startup, so an
# incompatible database makes the process fail to boot instead of failing lazily
# on the first request that touches it.
db.connect().close()

app = FastAPI(title="wiki")
app.include_router(notes.router)
app.include_router(search.router)
app.include_router(config.router)
app.include_router(version.router)


@app.get("/{full_path:path}")
def spa(full_path: str):
    candidate = STATIC_DIR / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(STATIC_DIR / "index.html")
