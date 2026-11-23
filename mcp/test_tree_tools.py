"""Tests for the knowledge-tree tools (get_note_ancestors, get_note_children,
set_note_parent, search_containers) and the scope/kind arguments added to the
existing list and search tools. Each tool is called directly as the plain
async function @mcp_server.tool() leaves callable, against a mocked
transport -- same approach as test_notes_tools.py."""

from __future__ import annotations

import json

import httpx
import pytest

import server
from mcp.server.mcpserver.exceptions import ToolError

VALID_ID = "11111111-1111-1111-1111-111111111111"
PARENT_ID = "33333333-3333-3333-3333-333333333333"


# --- get_note_ancestors ----------------------------------------------------


@pytest.mark.asyncio
async def test_get_note_ancestors_returns_the_chain_root_first(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/notes/{VALID_ID}/ancestors"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": PARENT_ID,
                        "title": "education",
                        "kind": "container",
                        "content": "# education",
                        "content_truncated": False,
                    },
                    {
                        "id": VALID_ID,
                        "title": "UH-100",
                        "kind": "container",
                        "content": "# UH-100\n\nA 2018 college survey course.",
                        "content_truncated": False,
                    },
                ]
            },
        )

    install_client(handler)
    got = await server.get_note_ancestors(note_id=VALID_ID)

    assert [a.title for a in got.items] == ["education", "UH-100"]
    assert "2018 college survey course" in got.items[1].content


@pytest.mark.asyncio
async def test_get_note_ancestors_empty_for_a_top_level_note(install_client):
    install_client(lambda request: httpx.Response(200, json={"items": []}))
    assert (await server.get_note_ancestors(note_id=VALID_ID)).items == []


@pytest.mark.asyncio
async def test_get_note_ancestors_surfaces_a_404_as_a_tool_error(install_client):
    install_client(lambda request: httpx.Response(404, text="note not found"))
    with pytest.raises(ToolError):
        await server.get_note_ancestors(note_id=VALID_ID)


@pytest.mark.asyncio
async def test_get_note_ancestors_rejects_a_malformed_id_before_requesting(install_client):
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"items": []})

    install_client(handler)
    with pytest.raises(ToolError):
        await server.get_note_ancestors(note_id="../../etc/passwd")
    assert not called


# --- get_note_children -----------------------------------------------------


@pytest.mark.asyncio
async def test_get_note_children_passes_paging_through(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/notes/{VALID_ID}/children"
        assert request.url.params["limit"] == "5"
        assert request.url.params["offset"] == "10"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": PARENT_ID,
                        "title": "Sub",
                        "kind": "container",
                        "preview": "# Sub",
                        "created_at": "x",
                    }
                ],
                "total": 11,
                "limit": 5,
                "offset": 10,
            },
        )

    install_client(handler)
    got = await server.get_note_children(note_id=VALID_ID, limit=5, offset=10)

    assert got.total == 11
    assert got.items[0].kind == "container"


# --- set_note_parent -------------------------------------------------------


@pytest.mark.asyncio
async def test_set_note_parent_files_a_note(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == f"/api/v1/notes/{VALID_ID}/parent"
        assert json.loads(request.content) == {"parent_id": PARENT_ID}
        return httpx.Response(200, json={"id": VALID_ID, "parent_id": PARENT_ID})

    install_client(handler)
    assert await server.set_note_parent(note_id=VALID_ID, parent_id=PARENT_ID) == VALID_ID


@pytest.mark.asyncio
async def test_set_note_parent_clears_only_when_asked_explicitly(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"parent_id": None}
        return httpx.Response(200, json={"id": VALID_ID, "parent_id": None})

    install_client(handler)
    assert await server.set_note_parent(note_id=VALID_ID, parent_id=None) == VALID_ID


@pytest.mark.asyncio
async def test_set_note_parent_requires_the_parent_argument(install_client):
    # It used to default to None, so a call meant as "file this under
    # something" that forgot the argument quietly unfiled the note instead.
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    install_client(handler)
    with pytest.raises(TypeError):
        await server.set_note_parent(note_id=VALID_ID)
    assert not called, "no request should reach the backend"


@pytest.mark.asyncio
async def test_set_note_parent_rejects_a_malformed_parent_id(install_client):
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    install_client(handler)
    with pytest.raises(ToolError):
        await server.set_note_parent(note_id=VALID_ID, parent_id="not-a-uuid")
    assert not called


@pytest.mark.asyncio
async def test_set_note_parent_surfaces_a_rejected_assignment(install_client):
    install_client(
        lambda request: httpx.Response(400, text="that would make a cycle")
    )
    with pytest.raises(ToolError, match="cycle"):
        await server.set_note_parent(note_id=VALID_ID, parent_id=PARENT_ID)


# --- search_containers -----------------------------------------------------


@pytest.mark.asyncio
async def test_search_containers_scopes_the_query(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/search"
        assert request.url.params["scope"] == "containers"
        assert request.url.params["q"] == "history"
        return httpx.Response(200, json={"items": [], "total": 0, "limit": 10, "offset": 0})

    install_client(handler)
    assert (await server.search_containers(query="history")).total == 0


# --- scope / kind on the existing tools ------------------------------------


@pytest.mark.asyncio
async def test_list_notes_defaults_to_notes_scope(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["scope"] == "notes"
        return httpx.Response(200, json={"items": [], "total": 0, "limit": 10, "offset": 0})

    install_client(handler)
    await server.list_notes()


@pytest.mark.asyncio
async def test_list_notes_can_fetch_the_whole_skeleton(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["scope"] == "containers"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": VALID_ID,
                        "title": "UH-100",
                        "path": "/",
                        "preview": "# UH-100",
                        "created_at": "x",
                        "kind": "container",
                        "parent_id": PARENT_ID,
                    }
                ],
                "total": 1,
                "limit": 100,
                "offset": 0,
            },
        )

    install_client(handler)
    got = await server.list_notes(limit=100, scope="containers")

    assert got.items[0].parent_id == PARENT_ID


@pytest.mark.asyncio
async def test_create_note_can_make_a_container(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["kind"] == "container"
        return httpx.Response(200, json={"id": "new-id"})

    install_client(handler)
    assert await server.create_note(content="# UH-100\n\nA course.", kind="container") == "new-id"


@pytest.mark.asyncio
async def test_create_note_surfaces_a_rejected_container(install_client):
    install_client(lambda request: httpx.Response(422, text="a container needs a title line"))
    with pytest.raises(ToolError, match="title line"):
        await server.create_note(content="   ", kind="container")


@pytest.mark.asyncio
async def test_search_notes_passes_scope_through(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/search"
        assert request.url.params["scope"] == "all"
        return httpx.Response(200, json={"items": [], "total": 0, "limit": 10, "offset": 0})

    install_client(handler)
    await server.search_notes(query="x", scope="all")


# --- breadcrumb on search hits ---------------------------------------------


@pytest.mark.asyncio
async def test_search_hits_carry_the_breadcrumb(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "items": [{
                "id": VALID_ID, "title": "Reconstruction essay", "path": "/",
                "preview": "# Reconstruction essay", "score": 2.0, "created_at": "x",
                "kind": "note", "parent_id": PARENT_ID,
                "breadcrumb": "School › UH-100 › US History Foundations",
            }],
            "total": 1, "limit": 10, "offset": 0,
        })

    install_client(handler)
    got = await server.search_notes(query="Reconstruction")

    hit = got.items[0]
    assert hit.kind == "note"
    assert hit.parent_id == PARENT_ID
    assert hit.breadcrumb == "School › UH-100 › US History Foundations"


@pytest.mark.asyncio
async def test_search_hit_tolerates_a_backend_without_tree_fields(install_client):
    # A backend predating this change sends none of the three; parsing must
    # still succeed rather than failing the whole call.
    install_client(lambda request: httpx.Response(200, json={
        "items": [{"id": VALID_ID, "title": "t", "path": "/", "preview": "p",
                   "score": 1.0, "created_at": "x"}],
        "total": 1, "limit": 10, "offset": 0,
    }))

    hit = (await server.search_notes(query="q")).items[0]
    assert (hit.kind, hit.parent_id, hit.breadcrumb) == ("note", None, "")


@pytest.mark.asyncio
async def test_top_level_hit_reports_an_empty_breadcrumb(install_client):
    install_client(lambda request: httpx.Response(200, json={
        "items": [{"id": VALID_ID, "title": "t", "path": "/", "preview": "p",
                   "score": 1.0, "created_at": "x", "kind": "note",
                   "parent_id": None, "breadcrumb": ""}],
        "total": 1, "limit": 10, "offset": 0,
    }))

    hit = (await server.search_notes(query="q")).items[0]
    assert hit.breadcrumb == "" and hit.parent_id is None
