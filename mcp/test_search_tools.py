"""Tests for search_notes and search_notes_hybrid."""

from __future__ import annotations

import httpx
import pytest

import server
from mcp.server.mcpserver.exceptions import ToolError


@pytest.mark.asyncio
async def test_search_notes_success(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/search"
        assert request.url.params["q"] == "setup"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "n1",
                        "title": "Setup",
                        "path": "/docs/setup",
                        "preview": "...",
                        "score": 2,
                        "created_at": "x",
                    }
                ],
                "total": 1,
                "limit": 10,
                "offset": 0,
            },
        )

    install_client(handler)
    result = await server.search_notes(query="setup", limit=10, offset=0)
    assert result.total == 1
    assert result.items[0].id == "n1"


@pytest.mark.asyncio
async def test_search_notes_error(install_client):
    install_client(lambda request: httpx.Response(500, text="search index down"))
    with pytest.raises(ToolError, match="500"):
        await server.search_notes(query="x", limit=10, offset=0)


@pytest.mark.asyncio
async def test_search_notes_hybrid_success(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/search/hybrid"
        return httpx.Response(200, json={"items": [], "total": 0, "limit": 10, "offset": 0})

    install_client(handler)
    result = await server.search_notes_hybrid(query="x", limit=10, offset=0)
    assert result.total == 0


@pytest.mark.asyncio
async def test_search_notes_hybrid_error(install_client):
    install_client(lambda request: httpx.Response(503, text="qdrant unavailable"))
    with pytest.raises(ToolError, match="503"):
        await server.search_notes_hybrid(query="x", limit=10, offset=0)
