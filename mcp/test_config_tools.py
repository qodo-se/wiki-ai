"""Tests for get_config, set_config, and reindex."""

from __future__ import annotations

import json

import httpx
import pytest

import server
from mcp.server.mcpserver.exceptions import ToolError

SAMPLE_CONFIG = {
    "ollama_url": "http://host.docker.internal:11434",
    "ollama_embedding_model": "nomic-embed-text",
    "ollama_chat_model": "llama3.1",
    "qdrant_url": "http://qdrant:6333",
    "qdrant_collection": "notes",
}


@pytest.mark.asyncio
async def test_get_config_success(install_client):
    install_client(lambda request: httpx.Response(200, json=SAMPLE_CONFIG))
    cfg = await server.get_config()
    assert cfg.qdrant_collection == "notes"


@pytest.mark.asyncio
async def test_get_config_error(install_client):
    install_client(lambda request: httpx.Response(500, text="config store unavailable"))
    with pytest.raises(ToolError, match="500"):
        await server.get_config()


@pytest.mark.asyncio
async def test_set_config_sends_only_given_fields(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {"ollama_chat_model": "llama3.2"}
        return httpx.Response(200, json={**SAMPLE_CONFIG, "ollama_chat_model": "llama3.2"})

    install_client(handler)
    cfg = await server.set_config(ollama_chat_model="llama3.2")
    assert cfg.ollama_chat_model == "llama3.2"


@pytest.mark.asyncio
async def test_set_config_requires_at_least_one_field(install_client):
    install_client(lambda request: httpx.Response(200, json=SAMPLE_CONFIG))
    with pytest.raises(ToolError, match="at least one field"):
        await server.set_config()


@pytest.mark.asyncio
async def test_set_config_error(install_client):
    install_client(lambda request: httpx.Response(400, text="unknown field"))
    with pytest.raises(ToolError, match="400"):
        await server.set_config(qdrant_url="http://bad")


@pytest.mark.asyncio
async def test_reindex_success(install_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/search/reindex"
        return httpx.Response(200, json={"total": 10, "embedded": 9, "failed": 1})

    install_client(handler)
    result = await server.reindex()
    assert result.total == 10
    assert result.failed == 1


@pytest.mark.asyncio
async def test_reindex_error(install_client):
    install_client(lambda request: httpx.Response(500, text="embedding model unavailable"))
    with pytest.raises(ToolError, match="500"):
        await server.reindex()
