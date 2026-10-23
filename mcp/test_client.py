"""Tests for client.py against a mocked wiki API -- no live backend needed."""

from __future__ import annotations

import json

import httpx
import pytest

from client import WikiAPIError, WikiClient

# Note ids must be well-formed UUIDs now that the client validates them before
# building a request path -- these two are arbitrary but valid-shaped
# placeholders (MISSING_ID doesn't need to "exist" anywhere; the mocked
# handler decides the response regardless of which valid id is passed).
VALID_ID = "11111111-1111-1111-1111-111111111111"
MISSING_ID = "22222222-2222-2222-2222-222222222222"


def make_client(handler) -> WikiClient:
    return WikiClient("http://wiki.test", transport=httpx.MockTransport(handler))


def json_response(status: int, payload) -> httpx.Response:
    return httpx.Response(status, json=payload)


@pytest.mark.asyncio
async def test_list_notes_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/notes"
        assert request.url.params["limit"] == "5"
        assert request.url.params["offset"] == "10"
        return json_response(
            200, {"items": [], "total": 0, "limit": 5, "offset": 10}
        )

    result = await make_client(handler).list_notes(5, 10)
    assert result.total == 0
    assert result.limit == 5


@pytest.mark.asyncio
async def test_list_notes_error_surfaces_status_and_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(WikiAPIError) as exc_info:
        await make_client(handler).list_notes(10, 0)
    assert exc_info.value.status_code == 500
    assert "boom" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_note_content_returns_raw_text():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/notes/{VALID_ID}"
        return httpx.Response(200, text="# hello wiki")

    content = await make_client(handler).get_note_content(VALID_ID)
    assert content == "# hello wiki"


@pytest.mark.asyncio
async def test_get_note_content_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="note not found")

    with pytest.raises(WikiAPIError) as exc_info:
        await make_client(handler).get_note_content(MISSING_ID)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_note_meta_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/notes/{VALID_ID}/meta"
        return json_response(
            200,
            {
                "id": VALID_ID,
                "title": "Hello",
                "path": "/docs/hello",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
            },
        )

    meta = await make_client(handler).get_note_meta(VALID_ID)
    assert meta.title == "Hello"
    assert meta.path == "/docs/hello"


@pytest.mark.asyncio
async def test_create_note_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/notes"
        body = json.loads(request.content)
        assert body == {"content": "hi", "path": "/docs/x"}
        return json_response(200, {"id": "new-id"})

    note_id = await make_client(handler).create_note("hi", "/docs/x")
    assert note_id == "new-id"


@pytest.mark.asyncio
async def test_create_note_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid path")

    with pytest.raises(WikiAPIError):
        await make_client(handler).create_note("hi", "")


@pytest.mark.asyncio
async def test_put_note_sends_content_and_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == f"/api/v1/notes/{VALID_ID}"
        body = json.loads(request.content)
        assert body == {"content": "updated", "path": "/docs/new"}
        return httpx.Response(200)

    await make_client(handler).put_note(VALID_ID, "updated", "/docs/new")


@pytest.mark.asyncio
async def test_put_note_404_on_missing_note():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with pytest.raises(WikiAPIError) as exc_info:
        await make_client(handler).put_note(MISSING_ID, "x", "/p")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_note_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == f"/api/v1/notes/{VALID_ID}"
        return httpx.Response(204)

    await make_client(handler).delete_note(VALID_ID)


@pytest.mark.asyncio
async def test_delete_note_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with pytest.raises(WikiAPIError):
        await make_client(handler).delete_note(MISSING_ID)


@pytest.mark.asyncio
async def test_search_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/search"
        assert request.url.params["q"] == "setup"
        return json_response(
            200,
            {
                "items": [
                    {
                        "id": "n1",
                        "title": "Setup guide",
                        "path": "/docs/setup",
                        "preview": "...setup...",
                        "score": 3,
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ],
                "total": 1,
                "limit": 10,
                "offset": 0,
            },
        )

    result = await make_client(handler).search("setup", 10, 0)
    assert result.total == 1
    assert result.items[0].id == "n1"


@pytest.mark.asyncio
async def test_search_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="search backend down")

    with pytest.raises(WikiAPIError):
        await make_client(handler).search("x", 10, 0)


@pytest.mark.asyncio
async def test_hybrid_search_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/search/hybrid"
        return json_response(200, {"items": [], "total": 0, "limit": 10, "offset": 0})

    result = await make_client(handler).hybrid_search("x", 10, 0)
    assert result.total == 0


@pytest.mark.asyncio
async def test_hybrid_search_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="qdrant unavailable")

    with pytest.raises(WikiAPIError):
        await make_client(handler).hybrid_search("x", 10, 0)


@pytest.mark.asyncio
async def test_get_config_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/config"
        return json_response(
            200,
            {
                "ollama_url": "http://host.docker.internal:11434",
                "ollama_embedding_model": "nomic-embed-text",
                "ollama_chat_model": "llama3.1",
                "qdrant_url": "http://qdrant:6333",
                "qdrant_collection": "notes",
            },
        )

    cfg = await make_client(handler).get_config()
    assert cfg.qdrant_collection == "notes"


@pytest.mark.asyncio
async def test_set_config_sends_only_given_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        body = json.loads(request.content)
        assert body == {"ollama_chat_model": "llama3.2"}
        return json_response(
            200,
            {
                "ollama_url": "http://host.docker.internal:11434",
                "ollama_embedding_model": "nomic-embed-text",
                "ollama_chat_model": "llama3.2",
                "qdrant_url": "http://qdrant:6333",
                "qdrant_collection": "notes",
            },
        )

    cfg = await make_client(handler).set_config({"ollama_chat_model": "llama3.2"})
    assert cfg.ollama_chat_model == "llama3.2"


@pytest.mark.asyncio
async def test_set_config_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="unknown field")

    with pytest.raises(WikiAPIError):
        await make_client(handler).set_config({"bogus": "x"})


@pytest.mark.asyncio
async def test_long_running_calls_actually_disable_the_timeout():
    """Regression test: _request used to do `kwargs.pop("timeout", None) or
    DEFAULT_TIMEOUT`, which silently discarded an explicit timeout=None (falsy)
    and reimposed the 10s default -- defeating reindex/backup/upload_image's
    LONG_TIMEOUT entirely. Assert the timeout actually reaching the transport
    is None (no timeout), not DEFAULT_TIMEOUT, for each long-running call."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        if request.url.path == "/api/v1/search/reindex":
            return json_response(200, {"total": 0, "embedded": 0, "failed": 0})
        if request.url.path == f"/api/v1/notes/{VALID_ID}/images":
            return json_response(200, {"id": "img-1", "url": "/x"})
        # /api/v1/backup: POST (trigger) needs JSON, GET (download) needs bytes
        if request.method == "POST":
            return json_response(200, {"filename": "x.zip", "size": 1})
        return httpx.Response(200, content=b"x")

    client = make_client(handler)

    await client.reindex()
    assert seen["timeout"] == {"connect": None, "read": None, "write": None, "pool": None}

    await client.create_backup()
    assert seen["timeout"] == {"connect": None, "read": None, "write": None, "pool": None}

    await client.upload_image(VALID_ID, "a.png", b"data")
    assert seen["timeout"] == {"connect": None, "read": None, "write": None, "pool": None}


@pytest.mark.asyncio
async def test_short_calls_keep_the_default_timeout():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return json_response(200, {"items": [], "total": 0, "limit": 10, "offset": 0})

    await make_client(handler).list_notes(10, 0)
    assert seen["timeout"] == {"connect": 10.0, "read": 10.0, "write": 10.0, "pool": 10.0}


@pytest.mark.asyncio
async def test_reindex_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/search/reindex"
        return json_response(200, {"total": 10, "embedded": 9, "failed": 1})

    result = await make_client(handler).reindex()
    assert result.total == 10
    assert result.failed == 1


@pytest.mark.asyncio
async def test_reindex_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="embedding model unavailable")

    with pytest.raises(WikiAPIError):
        await make_client(handler).reindex()


@pytest.mark.asyncio
async def test_create_backup_triggers_then_downloads():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return json_response(200, {"filename": "manual-backup.zip", "size": 3})
        return httpx.Response(
            200,
            content=b"PK\x03\x04",
            headers={"content-disposition": 'attachment; filename="manual-backup.zip"'},
        )

    filename, data = await make_client(handler).create_backup()
    assert calls == [("POST", "/api/v1/backup"), ("GET", "/api/v1/backup")]
    assert filename == "manual-backup.zip"
    assert data == b"PK\x03\x04"


@pytest.mark.asyncio
async def test_create_backup_falls_back_to_default_filename():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200)
        return httpx.Response(200, content=b"PK\x03\x04")

    filename, data = await make_client(handler).create_backup()
    assert filename == "wiki-backup.zip"


@pytest.mark.asyncio
async def test_create_backup_error_on_trigger():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="disk full")

    with pytest.raises(WikiAPIError):
        await make_client(handler).create_backup()


@pytest.mark.asyncio
async def test_upload_image_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/notes/{VALID_ID}/images"
        assert b'name="file"' in request.content
        assert b"photo.jpg" in request.content
        return json_response(200, {"id": "img-1", "url": "/api/v1/images/img-1"})

    img = await make_client(handler).upload_image(VALID_ID, "photo.jpg", b"fake-bytes")
    assert img.id == "img-1"
    assert img.url == "/api/v1/images/img-1"


@pytest.mark.asyncio
async def test_upload_image_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="note not found")

    with pytest.raises(WikiAPIError):
        await make_client(handler).upload_image(MISSING_ID, "photo.jpg", b"x")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_id",
    [
        "not-a-uuid",
        "",
        "11111111-1111-1111-1111-11111111111",  # one char short
        f"{VALID_ID}?x=1",  # would be read as a query string, not part of the id
        f"{VALID_ID}#frag",  # would be read as a fragment, not part of the id
        f"{VALID_ID}/../other",  # would be read as extra path segments
    ],
)
@pytest.mark.parametrize(
    "call",
    [
        lambda c, bad_id: c.get_note_content(bad_id),
        lambda c, bad_id: c.get_note_meta(bad_id),
        lambda c, bad_id: c.put_note(bad_id, "x", "/p"),
        lambda c, bad_id: c.delete_note(bad_id),
        lambda c, bad_id: c.upload_image(bad_id, "f.png", b"x"),
    ],
)
async def test_malformed_note_id_rejected_before_any_request(bad_id, call):
    """A note id with '?', '#', or '/' in it must never reach httpx as a path
    segment -- those characters would be read as the start of a query string,
    a fragment, or extra path segments instead of literal id characters."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not send a request for a malformed note id")

    with pytest.raises(ValueError, match="not a valid note id"):
        await call(make_client(handler), bad_id)
