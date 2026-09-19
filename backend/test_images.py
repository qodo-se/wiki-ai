import base64
import os

import pytest
from fastapi.testclient import TestClient

import db

# Smallest possible valid PNG: a single transparent pixel.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def client():
    # Imported lazily (not at module import time) so the temp_db fixture has
    # already pointed db.DB_PATH at a per-test tmp file before app.py's
    # module-level `db.connect().close()` runs — see test_app_startup.py for
    # the same constraint.
    import app as app_module

    return TestClient(app_module.app)


def _create_note(client):
    res = client.post("/api/v1/notes", json={"content": "hello"})
    assert res.status_code == 200
    return res.json()["id"]


def test_upload_and_fetch_image_round_trips_bytes_and_content_type(client):
    note_id = _create_note(client)
    res = client.post(
        "/api/v1/images",
        files={"file": ("pixel.png", PNG_BYTES, "image/png")},
        data={"note_id": note_id},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["url"] == f"/api/v1/images/{body['id']}"

    fetched = client.get(body["url"])
    assert fetched.status_code == 200
    assert fetched.content == PNG_BYTES
    assert fetched.headers["content-type"] == "image/png"
    assert fetched.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_upload_rejects_disallowed_content_type(client):
    note_id = _create_note(client)
    res = client.post(
        "/api/v1/images",
        files={"file": ("note.txt", b"not an image", "text/plain")},
        data={"note_id": note_id},
    )
    assert res.status_code == 415


def test_upload_rejects_unknown_note_id(client):
    res = client.post(
        "/api/v1/images",
        files={"file": ("pixel.png", PNG_BYTES, "image/png")},
        data={"note_id": "does-not-exist"},
    )
    assert res.status_code == 404


def test_get_unknown_image_is_404(client):
    res = client.get("/api/v1/images/does-not-exist")
    assert res.status_code == 404


def test_delete_image_removes_it(client):
    note_id = _create_note(client)
    upload = client.post(
        "/api/v1/images",
        files={"file": ("pixel.png", PNG_BYTES, "image/png")},
        data={"note_id": note_id},
    ).json()
    on_disk = db.image_path(upload["id"], "image/png")
    assert os.path.exists(on_disk)

    res = client.delete(upload["url"])
    assert res.status_code == 204
    assert client.get(upload["url"]).status_code == 404
    assert client.delete(upload["url"]).status_code == 404
    assert not os.path.exists(on_disk)


def test_deleting_a_note_cascades_to_its_images(client):
    note_id = _create_note(client)
    upload = client.post(
        "/api/v1/images",
        files={"file": ("pixel.png", PNG_BYTES, "image/png")},
        data={"note_id": note_id},
    ).json()
    on_disk = db.image_path(upload["id"], "image/png")
    assert os.path.exists(on_disk)

    res = client.delete(f"/api/v1/notes/{note_id}")
    assert res.status_code == 204
    assert client.get(upload["url"]).status_code == 404
    assert not os.path.exists(on_disk)
