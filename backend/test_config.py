import pytest

import config


def test_defaults_when_unset():
    assert config.get_all_config() == config.DEFAULTS


def test_set_config_overrides_and_persists():
    updated = config.set_config({"ollama_url": "http://example:1234"})
    assert updated["ollama_url"] == "http://example:1234"
    assert updated["qdrant_collection"] == config.DEFAULTS["qdrant_collection"]
    assert config.get_all_config()["ollama_url"] == "http://example:1234"


def test_set_config_rejects_unknown_key():
    with pytest.raises(ValueError, match="not_a_real_key"):
        config.set_config({"not_a_real_key": "x"})


def test_set_config_upsert_updates_existing_value():
    config.set_config({"ollama_url": "http://first"})
    config.set_config({"ollama_url": "http://second"})
    assert config.get_config("ollama_url") == "http://second"
