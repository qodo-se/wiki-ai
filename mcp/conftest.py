"""Shared pytest fixtures for mcp/'s test suite."""

from __future__ import annotations

import httpx
import pytest

import server
from client import WikiClient


@pytest.fixture
def install_client(monkeypatch):
    """Swaps server.client for one backed by a mocked transport, so tool
    tests never touch a live backend. handler is an httpx.MockTransport-style
    callable: (httpx.Request) -> httpx.Response."""

    def _install(handler) -> WikiClient:
        test_client = WikiClient("http://wiki.test", transport=httpx.MockTransport(handler))
        monkeypatch.setattr(server, "client", test_client)
        return test_client

    return _install
