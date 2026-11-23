"""The SPA catch-all must serve the shell without becoming a file server.

app.py ends in a GET /{full_path:path} route so client-side routes like
/search and /notes/<id> survive a hard refresh. That route also serves real
static assets, which is what makes it worth pinning down: every request that
matches no API route arrives here with a caller-controlled path, and two
separate quirks of path handling let such a path leave STATIC_DIR unless
containment is checked on the resolved path.

Each case below writes its file outside the served tree and asserts the
contents never come back in a response, so a future refactor that drops the
containment check fails here rather than in production.
"""

import pathlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def static_dir(tmp_path, monkeypatch):
    """Point the app at a real temp static tree instead of the Docker path."""
    import app as app_module

    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>shell</title>")
    (root / "css").mkdir()
    (root / "css" / "styles.css").write_text("body{}")
    monkeypatch.setattr(app_module, "STATIC_DIR", root)
    monkeypatch.setattr(app_module, "_STATIC_ROOT", root.resolve())
    return root


@pytest.fixture
def client(static_dir):
    import app as app_module

    # raise_server_exceptions=False so a 500 surfaces as a response to assert
    # on rather than propagating out of the request and failing the test early.
    return TestClient(app_module.app, raise_server_exceptions=False)


def _secret(tmp_path):
    f = tmp_path / "secret.txt"
    f.write_text("SENSITIVE-DO-NOT-SERVE")
    return f


# --- the SPA fallback still works ------------------------------------------


def test_a_client_route_gets_the_shell(client):
    res = client.get("/search")

    assert res.status_code == 200
    assert "shell" in res.text


def test_the_root_gets_the_shell(client):
    assert "shell" in client.get("/").text


def test_a_real_static_asset_is_still_served(client):
    res = client.get("/css/styles.css")

    assert res.status_code == 200
    assert res.text == "body{}"


# --- but it is not a file server -------------------------------------------


def test_an_encoded_traversal_does_not_escape_the_static_dir(client, tmp_path):
    # An encoded separator survives the ASGI layer's normalization of a
    # relative segment and is decoded only when this path parameter is
    # populated, so full_path can still carry one by the time it is joined
    # onto STATIC_DIR. Without containment this served the file with a 200.
    secret = _secret(tmp_path)

    res = client.get("/..%2fsecret.txt")

    assert res.status_code == 200, "falls back to the shell rather than erroring"
    assert secret.read_text() not in res.text
    assert "shell" in res.text


def test_a_deep_encoded_traversal_does_not_escape(client, tmp_path):
    secret = _secret(tmp_path)

    res = client.get("/css/..%2f..%2fsecret.txt")

    assert secret.read_text() not in res.text
    assert "shell" in res.text


def test_an_absolute_path_does_not_escape_the_static_dir(client, tmp_path):
    # pathlib's "/" discards the left operand when the right side is absolute,
    # so joining an absolute path onto STATIC_DIR yields that path unchanged
    # -- a second way out that a prefix check on the raw path would miss.
    secret = _secret(tmp_path)

    res = client.get(f"/{secret}")

    assert secret.read_text() not in res.text
    assert "shell" in res.text


def test_a_symlink_out_of_the_static_dir_is_not_followed(client, static_dir, tmp_path):
    secret = _secret(tmp_path)
    (static_dir / "escape.txt").symlink_to(secret)

    res = client.get("/escape.txt")

    assert secret.read_text() not in res.text
    assert "shell" in res.text


def test_a_directory_is_not_served_as_a_file(client):
    assert "shell" in client.get("/css").text


# --- unknown API paths 404 instead of getting the shell --------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/totallyfake",
        "/api/v2/search",
        "/api/v1/notes/zzz/nonsense",
        "/api/",
        # A retired endpoint a stale client may still be calling. Before this,
        # the two search modes collapsing into one left /hybrid answering with
        # the HTML shell and a 200, so such a client discovered the removal
        # only by failing to parse the page as JSON.
        "/api/v1/search/hybrid",
    ],
)
def test_an_unknown_api_path_404s(client, path):
    res = client.get(path)

    assert res.status_code == 404
    assert res.json()["detail"] == "no such API endpoint"


def test_a_real_api_route_still_wins_over_the_404_catch_all(client):
    # The 404 route is registered after every API router, so a live endpoint
    # must still reach its own handler. If registration order ever flips, this
    # is what notices.
    res = client.get("/api/v1/version")

    assert res.status_code == 200
    assert "version" in res.json()


def test_a_real_api_route_with_bad_params_still_validates(client):
    # 422 from the route's own validation, not 404 from the catch-all.
    assert client.get("/api/v1/notes?scope=sideways").status_code == 422
