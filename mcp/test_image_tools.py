"""Tests for upload_image -- verifies it takes base64 content + a filename
rather than a server-local path."""

from __future__ import annotations

import base64

import httpx
import pytest

import server
from mcp.server.mcpserver.exceptions import ToolError


@pytest.mark.asyncio
async def test_upload_image_success(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/notes/abc/images"
        assert b'name="file"' in request.content
        assert b'filename="photo.jpg"' in request.content
        assert b"not-really-a-jpeg" in request.content
        return httpx.Response(200, json={"id": "img-1", "url": "/api/v1/images/img-1"})

    install_client(handler)
    content_b64 = base64.b64encode(b"not-really-a-jpeg").decode()
    img = await server.upload_image(note_id="abc", filename="photo.jpg", content_base64=content_b64)
    assert img.id == "img-1"
    assert img.url == "/api/v1/images/img-1"


@pytest.mark.asyncio
async def test_upload_image_missing_note_surfaces_404(install_client):
    install_client(lambda request: httpx.Response(404, text="note not found"))
    content_b64 = base64.b64encode(b"x").decode()
    with pytest.raises(ToolError, match="404"):
        await server.upload_image(note_id="missing", filename="x.png", content_base64=content_b64)
