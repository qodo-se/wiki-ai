"""MCP server exposing wiki-ai's HTTP API as tools -- a 1:1 mirror of wiki-cli's
command set, wrapping the same /api/v1 endpoints.

Config (all via environment):
  WIKI_URL   base URL of the wiki API (default http://localhost:8081, matching
             wiki-cli's own default/override)
  MCP_HOST   interface this server binds to (default 0.0.0.0)
  MCP_PORT   port this server listens on (default 8082)
"""

from __future__ import annotations

import base64
import os

import mcp.types as types
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from client import WikiAPIError, WikiClient
from models import (
    AppConfig,
    NoteListResponse,
    NoteMeta,
    ReindexResult,
    SearchResponse,
    UploadedImage,
)

WIKI_URL = os.environ.get("WIKI_URL", "http://localhost:8081")
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8082"))

mcp_server = MCPServer(
    name="wiki-ai",
    instructions=(
        "Tools for reading, searching, and managing notes in a self-hosted "
        "wiki-ai instance. Mirrors the wiki-cli command set 1:1 against the "
        "same /api/v1 HTTP API."
    ),
)
client = WikiClient(WIKI_URL)


async def _call(coro):
    """Awaits a client call, turning a WikiAPIError into a ToolError so it
    surfaces as a clean MCP tool error (is_error=True with the message) rather
    than an unhandled exception -- mirrors wiki-cli's apiError formatting."""
    try:
        return await coro
    except WikiAPIError as e:
        raise ToolError(str(e)) from e


@mcp_server.tool()
async def list_notes(limit: int = 10, offset: int = 0) -> NoteListResponse:
    """List notes, most recently created first, paged by limit/offset."""
    return await _call(client.list_notes(limit, offset))


@mcp_server.tool()
async def get_note(note_id: str) -> str:
    """Get a note's raw markdown content by id."""
    return await _call(client.get_note_content(note_id))


@mcp_server.tool()
async def get_note_meta(note_id: str) -> NoteMeta:
    """Get a note's metadata (title, path, timestamps) by id."""
    return await _call(client.get_note_meta(note_id))


@mcp_server.tool()
async def create_note(content: str, path: str = "/") -> str:
    """Create a note with the given content at path. The server allocates
    and returns the note's id."""
    return await _call(client.create_note(content, path))


@mcp_server.tool()
async def update_note(note_id: str, content: str, path: str | None = None) -> str:
    """Update an existing note's content. If path is omitted, the note's
    current path is kept unchanged (fetches it first), matching wiki-cli's
    update command -- this never creates a note; it 404s if note_id doesn't
    already exist."""
    note_path = path
    if note_path is None:
        meta = await _call(client.get_note_meta(note_id))
        note_path = meta.path
    await _call(client.put_note(note_id, content, note_path))
    return note_id


@mcp_server.tool()
async def delete_note(note_id: str) -> str:
    """Delete a note by id."""
    await _call(client.delete_note(note_id))
    return "deleted"


@mcp_server.tool()
async def search_notes(query: str, limit: int = 10, offset: int = 0) -> SearchResponse:
    """Keyword (whole-word match) search over notes."""
    return await _call(client.search(query, limit, offset))


@mcp_server.tool()
async def search_notes_hybrid(query: str, limit: int = 10, offset: int = 0) -> SearchResponse:
    """Keyword + semantic search, fused by reciprocal rank fusion -- catches
    notes an exact keyword match would miss semantically, and vice versa."""
    return await _call(client.hybrid_search(query, limit, offset))


@mcp_server.tool()
async def get_config() -> AppConfig:
    """Get the server's current Ollama/Qdrant settings."""
    return await _call(client.get_config())


@mcp_server.tool()
async def set_config(
    ollama_url: str | None = None,
    ollama_embedding_model: str | None = None,
    ollama_chat_model: str | None = None,
    qdrant_url: str | None = None,
    qdrant_collection: str | None = None,
) -> AppConfig:
    """Update one or more server settings; only the fields passed are
    changed, every other setting is left untouched."""
    updates = {
        field: value
        for field, value in {
            "ollama_url": ollama_url,
            "ollama_embedding_model": ollama_embedding_model,
            "ollama_chat_model": ollama_chat_model,
            "qdrant_url": qdrant_url,
            "qdrant_collection": qdrant_collection,
        }.items()
        if value is not None
    }
    if not updates:
        raise ToolError("set_config requires at least one field to update")
    return await _call(client.set_config(updates))


@mcp_server.tool()
async def reindex() -> ReindexResult:
    """Drop and re-embed every note's semantic search vector (e.g. after
    changing the embedding model)."""
    return await _call(client.reindex())


@mcp_server.tool()
async def create_backup() -> list[types.ContentBlock]:
    """Trigger a fresh server-side backup and return it embedded in the
    response as a zip (database snapshot plus every note's uploaded images) --
    there is no server-local output path; the caller receives the bytes
    directly."""
    filename, data = await _call(client.create_backup())
    blob = base64.b64encode(data).decode()
    return [
        types.EmbeddedResource(
            resource=types.BlobResourceContents(
                uri=f"wiki-backup:///{filename}",
                mimeType="application/zip",
                blob=blob,
            )
        )
    ]


@mcp_server.tool()
async def upload_image(note_id: str, filename: str, content_base64: str) -> UploadedImage:
    """Upload base64-encoded file content as an image attached to a note,
    returning its id and the URL to insert into the note's markdown as
    ![alt](url). Takes content rather than a local path (unlike wiki-cli's
    upload-image flag) since this server may run in a container with no
    access to the caller's filesystem."""
    data = base64.b64decode(content_base64)
    return await _call(client.upload_image(note_id, filename, data))


if __name__ == "__main__":
    mcp_server.run(transport="streamable-http", host=MCP_HOST, port=MCP_PORT)
