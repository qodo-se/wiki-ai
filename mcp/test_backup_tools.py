"""Tests for create_backup -- verifies the zip comes back embedded in the
tool's return value rather than written to a server-local path."""

from __future__ import annotations

import base64

import httpx
import pytest

import mcp.types as types
import server
from mcp.server.mcpserver.exceptions import ToolError


@pytest.mark.asyncio
async def test_create_backup_returns_embedded_zip(install_client):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"filename": "manual-backup.zip", "size": 4})
        return httpx.Response(
            200,
            content=b"PK\x03\x04",
            headers={"content-disposition": 'attachment; filename="manual-backup.zip"'},
        )

    install_client(handler)
    blocks = await server.create_backup()

    assert calls == [("POST", "/api/v1/backup"), ("GET", "/api/v1/backup")]
    assert len(blocks) == 1
    block = blocks[0]
    assert isinstance(block, types.EmbeddedResource)
    assert block.resource.mime_type == "application/zip"
    assert block.resource.uri == "wiki-backup:///manual-backup.zip"
    assert base64.b64decode(block.resource.blob) == b"PK\x03\x04"


@pytest.mark.asyncio
async def test_create_backup_error_on_trigger(install_client):
    install_client(lambda request: httpx.Response(500, text="disk full"))
    with pytest.raises(ToolError, match="500"):
        await server.create_backup()
