import io
import sqlite3
import tempfile
import zipfile

import pytest
from fastapi.testclient import TestClient

import db


@pytest.fixture
def client():
    # Imported lazily (not at module import time) so the temp_db fixture has
    # already pointed db.DB_PATH at a per-test tmp file before app.py's
    # module-level `db.connect().close()` runs — see test_app_startup.py for
    # the same constraint.
    import app as app_module

    return TestClient(app_module.app)


def _notes_in(download_response) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(download_response.content)) as zf:
        db_bytes = zf.read("wiki.db")
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        f.write(db_bytes)
        f.flush()
        conn = sqlite3.connect(f.name)
        return {row[0] for row in conn.execute("SELECT content FROM notes")}


def test_download_before_any_backup_is_404(client):
    res = client.get("/api/v1/backup")
    assert res.status_code == 404


def test_create_backup_returns_filename_and_size(client):
    res = client.post("/api/v1/backup")
    assert res.status_code == 200
    body = res.json()
    assert body["filename"] == "manual-backup.zip"
    assert body["size"] > 0


def test_create_backup_returns_a_snapshot_id(client):
    res = client.post("/api/v1/backup")
    assert res.status_code == 200
    assert res.json()["snapshot_id"]


def test_each_create_returns_a_different_snapshot_id(client):
    first = client.post("/api/v1/backup").json()["snapshot_id"]
    second = client.post("/api/v1/backup").json()["snapshot_id"]
    assert first != second


def test_create_then_download_round_trips_a_real_snapshot(client):
    client.post("/api/v1/notes", json={"content": "hello backup"})
    assert client.post("/api/v1/backup").status_code == 200

    download_res = client.get("/api/v1/backup")
    assert download_res.status_code == 200
    assert download_res.headers["cache-control"] == "no-store"
    assert "hello backup" in _notes_in(download_res)


def test_second_create_overwrites_the_same_file(client):
    # No history, no timestamp — every create replaces the one file in place.
    client.post("/api/v1/notes", json={"content": "first"})
    first = client.post("/api/v1/backup")

    client.post("/api/v1/notes", json={"content": "second"})
    second = client.post("/api/v1/backup")

    assert first.json()["filename"] == second.json()["filename"] == "manual-backup.zip"
    assert _notes_in(client.get("/api/v1/backup")) == {"first", "second"}


def test_backup_zip_includes_note_images(client):
    note_id = client.post("/api/v1/notes", json={"content": "has an image"}).json()["id"]
    image_id = client.post(
        f"/api/v1/notes/{note_id}/images",
        files={"file": ("photo.png", b"fake-png-bytes", "image/png")},
    ).json()["id"]

    assert client.post("/api/v1/backup").status_code == 200
    download_res = client.get("/api/v1/backup")

    with zipfile.ZipFile(io.BytesIO(download_res.content)) as zf:
        assert zf.read(f"images/{image_id}.png") == b"fake-png-bytes"


def test_download_with_matching_snapshot_id_succeeds(client):
    client.post("/api/v1/notes", json={"content": "hello backup"})
    snapshot_id = client.post("/api/v1/backup").json()["snapshot_id"]

    res = client.get("/api/v1/backup", params={"snapshot_id": snapshot_id})
    assert res.status_code == 200
    assert "hello backup" in _notes_in(res)


def test_download_with_stale_snapshot_id_is_rejected_not_silently_served(client):
    # Backup A, then backup B overwrites the single file — downloading with
    # A's id must not silently hand back B's content.
    client.post("/api/v1/notes", json={"content": "first"})
    stale_id = client.post("/api/v1/backup").json()["snapshot_id"]

    client.post("/api/v1/notes", json={"content": "second"})
    client.post("/api/v1/backup")

    res = client.get("/api/v1/backup", params={"snapshot_id": stale_id})
    assert res.status_code == 409
    assert "superseded" in res.json()["detail"]


def test_download_with_unknown_snapshot_id_is_rejected(client):
    client.post("/api/v1/backup")
    res = client.get("/api/v1/backup", params={"snapshot_id": "not-a-real-id"})
    assert res.status_code == 409


def test_snapshot_id_is_persisted_to_disk_not_just_process_memory(client):
    # The whole point of tracking this in the database rather than a process-
    # global variable is that it survives a backend restart between a
    # caller's trigger and download. Proving that means reading it back from
    # the raw on-disk file directly, independent of any Python object this
    # test's own client happens to still be holding onto.
    snapshot_id = client.post("/api/v1/backup").json()["snapshot_id"]

    raw = sqlite3.connect(db.DB_PATH)
    try:
        row = raw.execute("SELECT snapshot_id FROM manual_backup_state").fetchone()
    finally:
        raw.close()
    assert row == (snapshot_id,)


def test_create_returns_409_while_a_backup_is_already_in_progress(client):
    # try_create_manual_backup() uses a non-blocking lock — a request arriving
    # while one is already running must be rejected immediately, not queued
    # behind it. Holds the lock directly to force that state deterministically
    # — racing two real threads against a backup of a tiny test database is
    # flaky, since the first can finish before the second even attempts to
    # acquire the lock.
    db._manual_backup_lock.acquire()
    try:
        res = client.post("/api/v1/backup")
        assert res.status_code == 409
    finally:
        db._manual_backup_lock.release()

    # Confirms the lock is actually released afterward, not just that the
    # busy response looks right.
    assert client.post("/api/v1/backup").status_code == 200
