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
import binascii
import os

import httpx
import mcp.types as types
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from client import WikiAPIError, WikiClient
from models import (
    AncestorsResponse,
    AppConfig,
    ChildListResponse,
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
        "same /api/v1 HTTP API.\n\n"
        "Notes are organized into a tree. A container is a note that groups "
        "other notes and whose body explains what that grouping is; an "
        "ordinary note can sit under one container. Containers are kept out "
        "of ordinary note results, so searching and listing never surfaces "
        "them unless you ask with scope.\n\n"
        "When a note's content alone doesn't say enough -- what project or "
        "course it belongs to, whether it is current or historical -- call "
        "get_note_ancestors on it. That returns the containers above it, "
        "root first, each with the prose describing what it is."
    ),
)
client = WikiClient(WIKI_URL)


async def _call(coro):
    """Awaits a client call, turning every failure a caller can't do anything
    about into a clean ToolError (is_error=True with a message) instead of an
    unhandled exception -- mirrors wiki-cli's apiError formatting. Covers:
    WikiAPIError (the backend responded, just not with success), ValueError
    (a request was rejected before it was even sent, e.g. a malformed note
    id), and httpx.HTTPError (no response came back at all -- the backend was
    unreachable or the request timed out)."""
    try:
        return await coro
    except (WikiAPIError, ValueError) as e:
        raise ToolError(str(e)) from e
    except httpx.HTTPError as e:
        raise ToolError(f"could not reach the wiki API at {WIKI_URL}: {e}") from e


@mcp_server.tool()
async def list_notes(
    limit: int = 10, offset: int = 0, scope: str = "notes"
) -> NoteListResponse:
    """List notes, most recently created first, paged by limit/offset.

    scope is "notes" (the default), "containers", or "all". Each item carries
    its kind and parent_id, so scope="containers" gives you the tree skeleton
    without walking it -- the parent_id values link the containers together.

    This is paged like any other listing: pass a large limit (100 is the
    maximum per request) and keep requesting with offset until you have
    `total` items. The default limit is 10, so a single default call shows
    only the first page of a larger tree."""
    return await _call(client.list_notes(limit, offset, scope))


@mcp_server.tool()
async def get_note(note_id: str) -> str:
    """Get a note's raw markdown content by id."""
    return await _call(client.get_note_content(note_id))


@mcp_server.tool()
async def get_note_meta(note_id: str) -> NoteMeta:
    """Get a note's metadata (title, path, timestamps) by id."""
    return await _call(client.get_note_meta(note_id))


@mcp_server.tool()
async def create_note(content: str, path: str = "/", kind: str = "note") -> str:
    """Create a note with the given content at path. The server allocates
    and returns the note's id.

    kind is "note" (the default) or "container". A container groups other
    notes, and its body is where you explain what the grouping is -- what the
    project or course was, when it ran, whether it is still current. That
    prose is what get_note_ancestors hands back as context, so write it for a
    reader who has never seen this wiki. A container must have a non-blank
    first line; that line becomes its name in the tree."""
    return await _call(client.create_note(content, path, kind))


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
async def search_notes(
    query: str, limit: int = 10, offset: int = 0, scope: str = "notes"
) -> SearchResponse:
    """Keyword (whole-word match) search over notes.

    scope is "notes" (the default), "containers", or "all".

    Each hit carries its tree position: kind, parent_id, and breadcrumb -- the
    titles of the containers above it, root first, like
    "School > UH-100 > US History Foundations" ("" for a top-level note).
    Use the breadcrumb to tell similar-looking hits apart before deciding
    which to read; call get_note_ancestors only when you need a container's
    full description rather than just its name."""
    return await _call(client.search(query, limit, offset, scope))


@mcp_server.tool()
async def search_notes_hybrid(
    query: str, limit: int = 10, offset: int = 0, scope: str = "notes"
) -> SearchResponse:
    """Keyword + semantic search, fused by reciprocal rank fusion -- catches
    notes an exact keyword match would miss semantically, and vice versa.

    scope is "notes" (the default), "containers", or "all". Hits carry the
    same kind/parent_id/breadcrumb tree position as search_notes."""
    return await _call(client.hybrid_search(query, limit, offset, scope))


@mcp_server.tool()
async def search_containers(query: str, limit: int = 10, offset: int = 0) -> SearchResponse:
    """Search only containers -- the notes that group other notes.

    Use this to find where something belongs, or what a part of the tree is
    about, without ordinary notes crowding the results."""
    return await _call(client.search(query, limit, offset, "containers"))


@mcp_server.tool()
async def get_note_ancestors(note_id: str) -> AncestorsResponse:
    """The chain of containers a note lives under, ordered root first and
    ending at its immediate parent.

    This is how you find out what a note actually is. A note filed under a
    container called "UH-100" tells you nothing on its own; the ancestors
    carry each container's prose -- that UH-100 was a 100-level college
    course taken in 2018, say, making its notes historical rather than
    current work. Call this before judging whether a note is relevant,
    current, or authoritative. Returns an empty list for a top-level note."""
    return await _call(client.get_note_ancestors(note_id))


@mcp_server.tool()
async def get_note_children(note_id: str, limit: int = 10, offset: int = 0) -> ChildListResponse:
    """One level down: the notes and containers filed directly under this
    note, containers first. Empty for an ordinary note, since only containers
    hold anything."""
    return await _call(client.get_note_children(note_id, limit, offset))


@mcp_server.tool()
async def set_note_parent(note_id: str, parent_id: str | None = None) -> str:
    """File a note under a container, or pass parent_id=None to move it back
    to the top level.

    The parent must be a container. A note has exactly one parent. Assignments
    that would make a cycle, point a note at itself, or nest deeper than 32
    levels are rejected."""
    await _call(client.set_note_parent(note_id, parent_id))
    return note_id


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
    try:
        data = base64.b64decode(content_base64, validate=True)
    except binascii.Error as e:
        raise ToolError(f"content_base64 is not valid base64: {e}") from e
    return await _call(client.upload_image(note_id, filename, data))


if __name__ == "__main__":
    mcp_server.run(transport="streamable-http", host=MCP_HOST, port=MCP_PORT)
