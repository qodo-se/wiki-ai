import pytest
from pydantic import ValidationError

from routes.config import ConfigBody


def test_rejects_blank_value():
    with pytest.raises(ValidationError):
        ConfigBody(ollama_embedding_model="   ")


def test_rejects_url_without_scheme():
    with pytest.raises(ValidationError):
        ConfigBody(ollama_url="host.docker.internal:11434")


def test_accepts_valid_url():
    body = ConfigBody(ollama_url="http://host.docker.internal:11434")
    assert body.ollama_url == "http://host.docker.internal:11434"


def test_unset_fields_stay_none():
    body = ConfigBody(qdrant_collection="notes")
    assert body.ollama_url is None
    assert body.qdrant_collection == "notes"
