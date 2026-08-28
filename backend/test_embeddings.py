import json
import urllib.error
from unittest.mock import patch

import config
import embeddings


class _FakeResp:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_embed_text_returns_vector():
    config.set_config({"ollama_url": "http://ollama-test:11434", "ollama_embedding_model": "test-model"})
    captured = {}

    def fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        return _FakeResp({"embedding": [0.1, 0.2, 0.3]})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        vector = embeddings.embed_text("hello")

    assert vector == [0.1, 0.2, 0.3]
    assert captured["url"] == "http://ollama-test:11434/api/embeddings"
    assert captured["body"] == {"model": "test-model", "prompt": "hello"}


def test_embed_text_raises_on_unreachable_host():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
        try:
            embeddings.embed_text("hello")
        except embeddings.EmbeddingError:
            pass
        else:
            assert False, "expected EmbeddingError"


def test_embed_text_raises_on_missing_embedding_field():
    with patch("urllib.request.urlopen", return_value=_FakeResp({"unexpected": True})):
        try:
            embeddings.embed_text("hello")
        except embeddings.EmbeddingError:
            pass
        else:
            assert False, "expected EmbeddingError"


def test_embed_text_raises_on_non_object_response():
    # A malformed/unexpected Ollama reply (e.g. a bare JSON array) must not
    # escape as a raw AttributeError from calling .get() on it.
    with patch("urllib.request.urlopen", return_value=_FakeResp([1, 2, 3])):
        try:
            embeddings.embed_text("hello")
        except embeddings.EmbeddingError:
            pass
        else:
            assert False, "expected EmbeddingError"
