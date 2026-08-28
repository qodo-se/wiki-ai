import socket

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


def test_resolves_to_link_local_true_for_metadata_ip():
    assert config.resolves_to_link_local("169.254.169.254") is True


def test_resolves_to_link_local_true_for_ipv4_mapped_ipv6(monkeypatch):
    monkeypatch.setattr(
        config.socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:169.254.169.254", 0, 0, 0))],
    )
    assert config.resolves_to_link_local("metadata.example") is True


def test_resolves_to_link_local_false_when_dns_fails(monkeypatch):
    def raise_gaierror(host, port):
        raise socket.gaierror("not found")

    monkeypatch.setattr(config.socket, "getaddrinfo", raise_gaierror)
    assert config.resolves_to_link_local("this-host-does-not-resolve.invalid") is False


def test_resolves_to_link_local_false_for_public_address(monkeypatch):
    monkeypatch.setattr(
        config.socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    assert config.resolves_to_link_local("example.com") is False


def test_assert_url_is_safe_rejects_link_local_url():
    with pytest.raises(config.UnsafeConfigURLError):
        config.assert_url_is_safe("http://169.254.169.254/latest/meta-data/")


def test_assert_url_is_safe_allows_ordinary_url(monkeypatch):
    monkeypatch.setattr(
        config.socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))],
    )
    config.assert_url_is_safe("http://internal-host:1234/path")
