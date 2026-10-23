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

# Note ids must be well-formed UUIDs now that the client validates them before
# building a request path (see test_client.py's
# test_malformed_note_id_rejected_before_any_request for that validation
# itself). MISSING_ID doesn't need to "exist" anywhere -- the mocked handler
# decides the response regardless of which valid id is passed.
VALID_ID = "11111111-1111-1111-1111-111111111111"
MISSING_ID = "22222222-2222-2222-2222-222222222222"


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
async def test_connection_failure_surfaces_as_tool_error(install_client):
    """Regression test: _call used to catch only WikiAPIError, which is
    raised after a response comes back with a bad status -- a connection
    failure or timeout (no response at all) escaped it as an unhandled httpx
    exception instead of a clean tool error."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    install_client(handler)
    with pytest.raises(ToolError, match="could not reach the wiki API"):
        await server.list_notes(limit=10, offset=0)


@pytest.mark.asyncio
async def test_get_note_returns_raw_content(install_client):
    install_client(lambda request: httpx.Response(200, text="# hello"))
    assert await server.get_note(note_id=VALID_ID) == "# hello"


@pytest.mark.asyncio
async def test_get_note_missing_surfaces_404(install_client):
    install_client(lambda request: httpx.Response(404, text="not found"))
    with pytest.raises(ToolError, match="404"):
        await server.get_note(note_id=MISSING_ID)


@pytest.mark.asyncio
async def test_get_note_malformed_id_surfaces_as_tool_error(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not send a request for a malformed note id")

    install_client(handler)
    with pytest.raises(ToolError, match="not a valid note id"):
        await server.get_note(note_id="not-a-uuid")


@pytest.mark.asyncio
async def test_get_note_meta_success(install_client):
    install_client(
        lambda request: httpx.Response(
            200,
            json={
                "id": VALID_ID,
                "title": "T",
                "path": "/p",
                "created_at": "x",
                "updated_at": "y",
            },
        )
    )
    meta = await server.get_note_meta(note_id=VALID_ID)
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
    note_id = await server.update_note(note_id=VALID_ID, content="new content", path="/docs/new")
    assert note_id == VALID_ID
    assert calls == [f"/api/v1/notes/{VALID_ID}"]  # no /meta lookup needed


@pytest.mark.asyncio
async def test_update_note_without_path_preserves_existing_path(install_client):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/meta"):
            return httpx.Response(
                200,
                json={
                    "id": VALID_ID,
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
    await server.update_note(note_id=VALID_ID, content="new content")
    assert ("GET", f"/api/v1/notes/{VALID_ID}/meta") in calls
    assert ("PUT", f"/api/v1/notes/{VALID_ID}") in calls


@pytest.mark.asyncio
async def test_update_note_404_on_missing_note(install_client):
    install_client(lambda request: httpx.Response(404, text="not found"))
    with pytest.raises(ToolError, match="404"):
        await server.update_note(note_id=MISSING_ID, content="x", path="/p")


@pytest.mark.asyncio
async def test_delete_note_success(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(204)

    install_client(handler)
    assert await server.delete_note(note_id=VALID_ID) == "deleted"


@pytest.mark.asyncio
async def test_delete_note_missing_surfaces_404(install_client):
    install_client(lambda request: httpx.Response(404, text="not found"))
    with pytest.raises(ToolError, match="404"):
        await server.delete_note(note_id=MISSING_ID)
