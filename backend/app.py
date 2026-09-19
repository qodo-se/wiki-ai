import pathlib

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_REQUEST_BODY = 10 * 1024 * 1024


class RequestSizeLimit:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        content_length = scope.get("headers", [])
        content_length = next((v for k, v in content_length if k.lower() == b"content-length"), None)
        if content_length is not None and int(content_length) > MAX_REQUEST_BODY:
            await send({"type": "http.response.start", "status": 413, "headers": []})
            await send({"type": "http.response.body", "body": b"request body too large"})
            return

        total = 0
        rejected = False
        async def limited_receive() -> Message:
            nonlocal total, rejected
            message = await receive()
            if message["type"] == "http.request" and not rejected:
                total += len(message.get("body", b""))
                if total > MAX_REQUEST_BODY:
                    rejected = True
                    await send({"type": "http.response.start", "status": 413, "headers": []})
                    await send({"type": "http.response.body", "body": b"request body too large"})
                    return {"type": "http.disconnect"}
            return message
        await self.app(scope, limited_receive, send)


from fastapi import FastAPI
from fastapi.responses import FileResponse

import db
from routes import config, images, notes, search, version

STATIC_DIR = pathlib.Path("/app/static")

# Runs schema migrations (and the downgrade guard) at process startup, so an
# incompatible database makes the process fail to boot instead of failing lazily
# on the first request that touches it.
db.connect().close()

app = FastAPI(title="wiki")
app.add_middleware(RequestSizeLimit)
app.include_router(notes.router)
app.include_router(images.router)
app.include_router(search.router)
app.include_router(config.router)
app.include_router(version.router)


@app.get("/{full_path:path}")
def spa(full_path: str):
    candidate = STATIC_DIR / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(STATIC_DIR / "index.html")
