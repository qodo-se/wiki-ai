"""Tests for the note CRUD tools (list_notes, get_note, get_note_meta,
create_note, update_note, delete_note). Each tool is called directly as the
plain async function the @mcp_server.tool() decorator leaves callable --
verifying the actual business logic (argument handling, error wrapping) the
same way an MCP client invoking it would see."""

from __future__ import annotations

import json

import httpx
import pytest

import server
from mcp.server.mcpserver.exceptions import ToolError


@pytest.mark.asyncio
async def test_list_notes_success(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["limit"] == "5"
        return httpx.Response(200, json={"items": [], "total": 0, "limit": 5, "offset": 0})

    install_client(handler)
    result = await server.list_notes(limit=5, offset=0)
    assert result.total == 0


@pytest.mark.asyncio
async def test_list_notes_error_surfaces_as_tool_error(install_client):
    install_client(lambda request: httpx.Response(500, text="db unavailable"))
    with pytest.raises(ToolError, match="500"):
        await server.list_notes(limit=10, offset=0)


@pytest.mark.asyncio
async def test_get_note_returns_raw_content(install_client):
    install_client(lambda request: httpx.Response(200, text="# hello"))
    assert await server.get_note(note_id="abc") == "# hello"


@pytest.mark.asyncio
async def test_get_note_missing_surfaces_404(install_client):
    install_client(lambda request: httpx.Response(404, text="not found"))
    with pytest.raises(ToolError, match="404"):
        await server.get_note(note_id="missing")


@pytest.mark.asyncio
async def test_get_note_meta_success(install_client):
    install_client(
        lambda request: httpx.Response(
            200,
            json={
                "id": "abc",
                "title": "T",
                "path": "/p",
                "created_at": "x",
                "updated_at": "y",
            },
        )
    )
    meta = await server.get_note_meta(note_id="abc")
    assert meta.title == "T"


@pytest.mark.asyncio
async def test_create_note_returns_server_allocated_id(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {"content": "hi", "path": "/docs/x"}
        return httpx.Response(200, json={"id": "new-id"})

    install_client(handler)
    note_id = await server.create_note(content="hi", path="/docs/x")
    assert note_id == "new-id"


@pytest.mark.asyncio
async def test_update_note_with_explicit_path_skips_meta_lookup(install_client):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.method == "PUT"
        body = json.loads(request.content)
        assert body == {"content": "new content", "path": "/docs/new"}
        return httpx.Response(200)

    install_client(handler)
    note_id = await server.update_note(note_id="abc", content="new content", path="/docs/new")
    assert note_id == "abc"
    assert calls == ["/api/v1/notes/abc"]  # no /meta lookup needed


@pytest.mark.asyncio
async def test_update_note_without_path_preserves_existing_path(install_client):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/meta"):
            return httpx.Response(
                200,
                json={
                    "id": "abc",
                    "title": "T",
                    "path": "/docs/existing",
                    "created_at": "x",
                    "updated_at": "y",
                },
            )
        body = json.loads(request.content)
        assert body["path"] == "/docs/existing"
        return httpx.Response(200)

    install_client(handler)
    await server.update_note(note_id="abc", content="new content")
    assert ("GET", "/api/v1/notes/abc/meta") in calls
    assert ("PUT", "/api/v1/notes/abc") in calls


@pytest.mark.asyncio
async def test_update_note_404_on_missing_note(install_client):
    install_client(lambda request: httpx.Response(404, text="not found"))
    with pytest.raises(ToolError, match="404"):
        await server.update_note(note_id="missing", content="x", path="/p")


@pytest.mark.asyncio
async def test_delete_note_success(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(204)

    install_client(handler)
    assert await server.delete_note(note_id="abc") == "deleted"


@pytest.mark.asyncio
async def test_delete_note_missing_surfaces_404(install_client):
    install_client(lambda request: httpx.Response(404, text="not found"))
    with pytest.raises(ToolError, match="404"):
        await server.delete_note(note_id="missing")
