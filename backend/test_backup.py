import sqlite3
import tempfile

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
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        f.write(download_response.content)
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
    assert body["filename"] == "manual-backup.db"
    assert body["size"] > 0


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

    assert first.json()["filename"] == second.json()["filename"] == "manual-backup.db"
    assert _notes_in(client.get("/api/v1/backup")) == {"first", "second"}


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
