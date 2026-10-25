"""Async HTTP client for wiki-ai's /api/v1 API, mirroring cli/client.go's endpoint set."""

from __future__ import annotations

import mimetypes
import re

import httpx

from models import (
    AppConfig,
    NoteListResponse,
    NoteMeta,
    ReindexResult,
    SearchResponse,
    UploadedImage,
)

DEFAULT_TIMEOUT = httpx.Timeout(10.0)
# Backup, reindex, and image upload each scale with database/file size (this
# app puts no cap on either), so a fixed short deadline could abort a
# legitimately still-running request instead of a genuinely hung one -- same
# reasoning cli/client.go uses its separate, timeout-less backupHTTP client for.
LONG_TIMEOUT = None

# Mirrors cli/validate.go's uuidPattern/requireUUID: every note id the backend
# hands out is a UUID, and every note id this client is given ends up
# interpolated straight into a request path. Rejecting anything else here --
# before it reaches httpx -- is what stops a stray "?", "#", or "/" in a
# caller-supplied id from being read as a query string, fragment, or path
# separator instead of literal id characters.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _require_valid_note_id(note_id: str) -> str:
    if not _UUID_RE.match(note_id):
        raise ValueError(f"{note_id!r} is not a valid note id (expected a UUID)")
    return note_id


class WikiAPIError(Exception):
    """Raised when the wiki API responds with a non-2xx status."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"server returned {status_code}: {body}")


class WikiClient:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None):
        # transport is overridden in tests (httpx.MockTransport) to exercise this
        # client -- and everything built on it -- without a live backend.
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=DEFAULT_TIMEOUT, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, *, timeout: httpx.Timeout | None = DEFAULT_TIMEOUT, **kwargs
    ) -> httpx.Response:
        # timeout defaults to DEFAULT_TIMEOUT when the caller doesn't pass it at
        # all, but an explicit timeout=None (LONG_TIMEOUT) must reach httpx as
        # None -- `kwargs.pop("timeout", None) or DEFAULT_TIMEOUT` would silently
        # discard that explicit None (falsy) and reimpose DEFAULT_TIMEOUT, which
        # is exactly the bug this keyword-only parameter avoids.
        resp = await self._http.request(method, path, timeout=timeout, **kwargs)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise WikiAPIError(resp.status_code, resp.text)
        return resp

    async def list_notes(self, limit: int, offset: int) -> NoteListResponse:
        resp = await self._request(
            "GET", "/api/v1/notes", params={"limit": limit, "offset": offset}
        )
        return NoteListResponse.model_validate(resp.json())

    async def get_note_content(self, note_id: str) -> str:
        note_id = _require_valid_note_id(note_id)
        resp = await self._request("GET", f"/api/v1/notes/{note_id}")
        return resp.text

    async def get_note_meta(self, note_id: str) -> NoteMeta:
        note_id = _require_valid_note_id(note_id)
        resp = await self._request("GET", f"/api/v1/notes/{note_id}/meta")
        return NoteMeta.model_validate(resp.json())

    # create_note asks the server to allocate a new note id; the client never
    # picks the id itself.
    async def create_note(self, content: str, path: str) -> str:
        resp = await self._request(
            "POST", "/api/v1/notes", json={"content": content, "path": path}
        )
        return resp.json()["id"]

    async def put_note(self, note_id: str, content: str, path: str) -> None:
        note_id = _require_valid_note_id(note_id)
        await self._request(
            "PUT", f"/api/v1/notes/{note_id}", json={"content": content, "path": path}
        )

    async def delete_note(self, note_id: str) -> None:
        note_id = _require_valid_note_id(note_id)
        await self._request("DELETE", f"/api/v1/notes/{note_id}")

    async def search(self, query: str, limit: int, offset: int) -> SearchResponse:
        resp = await self._request(
            "GET", "/api/v1/search", params={"q": query, "limit": limit, "offset": offset}
        )
        return SearchResponse.model_validate(resp.json())

    async def hybrid_search(self, query: str, limit: int, offset: int) -> SearchResponse:
        resp = await self._request(
            "GET",
            "/api/v1/search/hybrid",
            params={"q": query, "limit": limit, "offset": offset},
        )
        return SearchResponse.model_validate(resp.json())

    async def get_config(self) -> AppConfig:
        resp = await self._request("GET", "/api/v1/config")
        return AppConfig.model_validate(resp.json())

    # set_config only sends the fields present in updates -- the server
    # leaves every other setting untouched, same as a partial PUT.
    async def set_config(self, updates: dict[str, str]) -> AppConfig:
        resp = await self._request("PUT", "/api/v1/config", json=updates)
        return AppConfig.model_validate(resp.json())

    async def reindex(self) -> ReindexResult:
        resp = await self._request("POST", "/api/v1/search/reindex", timeout=LONG_TIMEOUT)
        return ReindexResult.model_validate(resp.json())

    # create_backup triggers a fresh server-side snapshot and immediately
    # downloads it, combining what the CLI does as two client-side steps
    # (trigger, then download) into one call -- there's no use case for
    # triggering without retrieving, or retrieving without a fresh trigger.
    #
    # The trigger's snapshot_id is passed through to the download as a query
    # param so the server can detect if some other trigger (another MCP call,
    # the CLI, a future scheduled job) has overwritten the single backup file
    # in between -- if it has, the server responds 409 (surfaced by _request
    # as WikiAPIError, then wrapped into a clean ToolError by server.py's
    # _call(), same as any other failure) instead of silently handing back a
    # different snapshot than the one this call just triggered.
    async def create_backup(self) -> tuple[str, bytes]:
        trigger = await self._request("POST", "/api/v1/backup", timeout=LONG_TIMEOUT)
        # .get(), not [...]: an older/mismatched backend predating snapshot_id
        # still returns 200 with just filename/size, and downloading without
        # the param falls back to today's "whatever's currently on disk"
        # behavior rather than raising on the missing key.
        snapshot_id = trigger.json().get("snapshot_id")
        params = {"snapshot_id": snapshot_id} if snapshot_id else None
        resp = await self._request(
            "GET",
            "/api/v1/backup",
            params=params,
            timeout=LONG_TIMEOUT,
        )
        filename = (
            _filename_from_content_disposition(resp.headers.get("content-disposition", ""))
            or "wiki-backup.zip"
        )
        return filename, resp.content

    async def upload_image(self, note_id: str, filename: str, content: bytes) -> UploadedImage:
        note_id = _require_valid_note_id(note_id)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        resp = await self._request(
            "POST",
            f"/api/v1/notes/{note_id}/images",
            files={"file": (filename, content, content_type)},
            timeout=LONG_TIMEOUT,
        )
        return UploadedImage.model_validate(resp.json())


def _filename_from_content_disposition(header: str) -> str | None:
    # e.g. Content-Disposition: attachment; filename="manual-backup.zip"
    for part in header.split(";"):
        part = part.strip()
        if part.lower().startswith("filename="):
            return part.split("=", 1)[1].strip('"')
    return None
