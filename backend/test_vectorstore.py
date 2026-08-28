import json
import urllib.error
from unittest.mock import patch

import config
import vectorstore


class _FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(url, code, body):
    err = urllib.error.HTTPError(url, code, "error", None, None)
    err.read = lambda: json.dumps(body).encode()
    return err


def test_search_vectors_returns_empty_when_collection_missing():
    config.set_config({"qdrant_url": "http://qdrant-test:6333", "qdrant_collection": "notes"})

    def fake_urlopen(req, timeout=30):
        raise _http_error(req.full_url, 404, {"status": {"error": "not found"}})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert vectorstore.search_vectors([0.1, 0.2], 5) == []


def test_upsert_vector_creates_collection_then_upserts():
    config.set_config({"qdrant_url": "http://qdrant-test:6333", "qdrant_collection": "notes"})
    calls = []

    def fake_urlopen(req, timeout=30):
        calls.append((req.get_method(), req.full_url))
        if req.get_method() == "GET":
            raise _http_error(req.full_url, 404, {})
        return _FakeResp(200, {"result": True, "status": "ok"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        vectorstore.upsert_vector("note-1", [0.1, 0.2, 0.3], {"path": "/x"})

    assert ("GET", "http://qdrant-test:6333/collections/notes") in calls
    assert ("PUT", "http://qdrant-test:6333/collections/notes") in calls
    assert ("PUT", "http://qdrant-test:6333/collections/notes/points") in calls


def test_vectorstore_error_on_unreachable_host():
    config.set_config({"qdrant_url": "http://qdrant-test:6333"})
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
        try:
            vectorstore.search_vectors([0.1], 5)
        except vectorstore.VectorStoreError:
            pass
        else:
            assert False, "expected VectorStoreError"


def test_upsert_vector_rejects_dimension_mismatch_with_existing_collection():
    # The collection already stores 3-dim vectors (e.g. from an earlier
    # embedding model); a newly-configured model producing 4-dim vectors
    # must fail loudly instead of corrupting/crashing the collection.
    config.set_config({"qdrant_url": "http://qdrant-test:6333", "qdrant_collection": "notes"})

    def fake_urlopen(req, timeout=30):
        assert req.get_method() == "GET"
        return _FakeResp(200, {"result": {"config": {"params": {"vectors": {"size": 3}}}}})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        try:
            vectorstore.upsert_vector("note-1", [0.1, 0.2, 0.3, 0.4])
        except vectorstore.VectorStoreError as e:
            assert "3-dimension" in str(e) and "4 dimensions" in str(e)
        else:
            assert False, "expected VectorStoreError"


def test_search_vectors_rejects_response_missing_result_key():
    config.set_config({"qdrant_url": "http://qdrant-test:6333", "qdrant_collection": "notes"})
    with patch("urllib.request.urlopen", return_value=_FakeResp(200, {"unexpected": True})):
        try:
            vectorstore.search_vectors([0.1], 5)
        except vectorstore.VectorStoreError:
            pass
        else:
            assert False, "expected VectorStoreError"


def test_request_survives_non_json_error_body():
    config.set_config({"qdrant_url": "http://qdrant-test:6333", "qdrant_collection": "notes"})

    def fake_urlopen(req, timeout=30):
        err = urllib.error.HTTPError(req.full_url, 500, "error", None, None)
        err.read = lambda: b"<html>not json</html>"
        raise err

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        try:
            vectorstore.search_vectors([0.1], 5)
        except vectorstore.VectorStoreError:
            pass
        else:
            assert False, "expected VectorStoreError"
