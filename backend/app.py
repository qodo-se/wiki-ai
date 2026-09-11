import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from db import connect
from fastapi.responses import FileResponse

from routes import config, notes, search

STATIC_DIR = pathlib.Path("/app/static")

@asynccontextmanager
async def lifespan(_app):
    with connect():
        pass
    yield


app = FastAPI(title="wiki", lifespan=lifespan)
app.include_router(notes.router)
app.include_router(search.router)
app.include_router(config.router)


@app.get("/{full_path:path}")
def spa(full_path: str):
    candidate = STATIC_DIR / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(STATIC_DIR / "index.html")
