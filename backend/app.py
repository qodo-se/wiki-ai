import pathlib

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

import db
from routes import backup, config, images, notes, search, version

STATIC_DIR = pathlib.Path("/app/static")
# Resolved once at import: the SPA fallback compares every candidate against
# this, and resolving the root per request would be a syscall on every page load.
_STATIC_ROOT = STATIC_DIR.resolve()

# Runs schema migrations (and the downgrade guard) at process startup, so an
# incompatible database makes the process fail to boot instead of failing lazily
# on the first request that touches it.
db.connect().close()

app = FastAPI(title="wiki")
app.include_router(notes.router)
app.include_router(images.router)
app.include_router(search.router)
app.include_router(config.router)
app.include_router(version.router)
app.include_router(backup.router)


# Registered after every API router and before the SPA fallback, so a real API
# route still wins but an unknown /api/... path 404s instead of being handed
# the HTML shell with a 200. A caller that typo'd an endpoint, or is still on a
# retired one, otherwise has to discover the failure by failing to parse the
# page as JSON. GET-only, matching the fallback below: other methods already
# 405 on an unmatched path, since nothing registers them.
@app.get("/api/{_:path}")
def api_not_found(_: str):
    raise HTTPException(status_code=404, detail="no such API endpoint")


@app.get("/{full_path:path}")
def spa(full_path: str):
    # The served file must be confined to STATIC_DIR, and containment has to
    # be checked on the *resolved* path — a prefix test against the raw
    # full_path looks equivalent and is not. Two ways out of the directory
    # close here, neither of them visible before the path is resolved:
    #
    #  - An encoded path separator survives the ASGI layer's normalization of
    #    a relative segment and is decoded only later, when this path
    #    parameter is populated, so full_path can still carry a relative
    #    segment by the time it is joined on below.
    #  - pathlib's "/" discards the left operand entirely when the right side
    #    is absolute, so joining an absolute path on yields it unchanged.
    #
    # resolve() also collapses symlinks, so a link inside STATIC_DIR pointing
    # outside it doesn't become a way through either.
    candidate = (STATIC_DIR / full_path).resolve()
    if full_path and candidate.is_relative_to(_STATIC_ROOT) and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(STATIC_DIR / "index.html")
