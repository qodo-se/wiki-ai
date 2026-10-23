"""Tests for upload_image -- verifies it takes base64 content + a filename
rather than a server-local path."""

from __future__ import annotations

import base64

import httpx
import pytest

import server
from mcp.server.mcpserver.exceptions import ToolError

# Note ids must be well-formed UUIDs now that the client validates them before
# building a request path. MISSING_ID doesn't need to "exist" anywhere -- the
# mocked handler decides the response regardless of which valid id is passed.
VALID_ID = "11111111-1111-1111-1111-111111111111"
MISSING_ID = "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_upload_image_success(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/notes/{VALID_ID}/images"
        assert b'name="file"' in request.content
        assert b'filename="photo.jpg"' in request.content
        assert b"not-really-a-jpeg" in request.content
        return httpx.Response(200, json={"id": "img-1", "url": "/api/v1/images/img-1"})

    install_client(handler)
    content_b64 = base64.b64encode(b"not-really-a-jpeg").decode()
    img = await server.upload_image(note_id=VALID_ID, filename="photo.jpg", content_base64=content_b64)
    assert img.id == "img-1"
    assert img.url == "/api/v1/images/img-1"


@pytest.mark.asyncio
async def test_upload_image_missing_note_surfaces_404(install_client):
    install_client(lambda request: httpx.Response(404, text="note not found"))
    content_b64 = base64.b64encode(b"x").decode()
    with pytest.raises(ToolError, match="404"):
        await server.upload_image(note_id=MISSING_ID, filename="x.png", content_base64=content_b64)


@pytest.mark.asyncio
async def test_upload_image_invalid_base64_surfaces_as_tool_error(install_client):
    # Never reaches the backend -- decoding fails before any request is sent.
    install_client(lambda request: httpx.Response(500, text="should not be called"))
    with pytest.raises(ToolError, match="not valid base64"):
        await server.upload_image(
            note_id=VALID_ID, filename="x.png", content_base64="not-valid-base64!!"
        )


@pytest.mark.asyncio
async def test_upload_image_malformed_note_id_surfaces_as_tool_error(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not send a request for a malformed note id")

    install_client(handler)
    content_b64 = base64.b64encode(b"x").decode()
    with pytest.raises(ToolError, match="not a valid note id"):
        await server.upload_image(note_id="not-a-uuid", filename="x.png", content_base64=content_b64)
