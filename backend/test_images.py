import os

import pytest
from fastapi.testclient import TestClient

import db


@pytest.fixture
def client():
    # Imported lazily so the temp_db fixture has already pointed db.DB_PATH at
    # a per-test tmp file before app.py's module-level `db.connect().close()`
    # runs — see test_backup.py for the same constraint.
    import app as app_module

    return TestClient(app_module.app)


def _create_note(client) -> str:
    res = client.post("/api/v1/notes", json={"content": "hello"})
    assert res.status_code == 200
    return res.json()["id"]


def test_upload_returns_id_and_a_fetchable_url(client):
    note_id = _create_note(client)
    res = client.post(
        f"/api/v1/notes/{note_id}/images",
        files={"file": ("photo.png", b"fake-png-bytes", "image/png")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"]
    assert body["url"] == f"/api/v1/images/{body['id']}"


def test_upload_to_a_missing_note_is_404(client):
    res = client.post(
        "/api/v1/notes/does-not-exist/images",
        files={"file": ("photo.png", b"bytes", "image/png")},
    )
    assert res.status_code == 404


def test_upload_accepts_any_content_type(client):
    # No MIME allowlist — an arbitrary/unusual content-type must still upload.
    note_id = _create_note(client)
    res = client.post(
        f"/api/v1/notes/{note_id}/images",
        files={"file": ("data.bin", b"\x00\x01arbitrary", "application/x-made-up")},
    )
    assert res.status_code == 200


def test_fetch_serves_the_uploaded_bytes_with_its_content_type(client):
    note_id = _create_note(client)
    image_id = client.post(
        f"/api/v1/notes/{note_id}/images",
        files={"file": ("photo.png", b"fake-png-bytes", "image/png")},
    ).json()["id"]

    res = client.get(f"/api/v1/images/{image_id}")
    assert res.status_code == 200
    assert res.content == b"fake-png-bytes"
    assert res.headers["content-type"] == "image/png"


def test_stored_file_keeps_the_original_extension(client):
    # Matters beyond just this app's own fetch endpoint: the manual backup zip
    # archives whatever's in images_dir() as-is, so an extension-less file on
    # disk means an extension-less file in every backup too.
    note_id = _create_note(client)
    image_id = client.post(
        f"/api/v1/notes/{note_id}/images",
        files={"file": ("vacation.JPEG", b"bytes", "image/jpeg")},
    ).json()["id"]

    assert os.path.exists(os.path.join(db.images_dir(), image_id + ".JPEG"))


def test_fetch_missing_image_is_404(client):
    assert client.get("/api/v1/images/does-not-exist").status_code == 404


def test_delete_removes_the_image(client):
    note_id = _create_note(client)
    image_id = client.post(
        f"/api/v1/notes/{note_id}/images",
        files={"file": ("photo.png", b"bytes", "image/png")},
    ).json()["id"]

    assert client.delete(f"/api/v1/images/{image_id}").status_code == 204
    assert client.get(f"/api/v1/images/{image_id}").status_code == 404


def test_delete_missing_image_is_404(client):
    assert client.delete("/api/v1/images/does-not-exist").status_code == 404


def test_deleting_a_note_cascades_to_its_images(client):
    note_id = _create_note(client)
    image_id = client.post(
        f"/api/v1/notes/{note_id}/images",
        files={"file": ("photo.png", b"bytes", "image/png")},
    ).json()["id"]

    assert client.delete(f"/api/v1/notes/{note_id}").status_code == 204
    assert client.get(f"/api/v1/images/{image_id}").status_code == 404
