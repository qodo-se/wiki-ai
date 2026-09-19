import sqlite3
from concurrent.futures import ThreadPoolExecutor

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


def test_download_latest_before_any_backup_is_404(client):
    res = client.get("/api/v1/backup/latest")
    assert res.status_code == 404


def test_create_backup_returns_filename_and_size(client):
    res = client.post("/api/v1/backup")
    assert res.status_code == 200
    body = res.json()
    assert body["filename"].startswith("wiki-v")
    assert body["filename"].endswith(".db")
    assert body["size"] > 0


def test_create_then_download_round_trips_a_real_snapshot(client, tmp_path):
    client.post("/api/v1/notes", json={"content": "hello backup"})
    create_res = client.post("/api/v1/backup")
    assert create_res.status_code == 200

    download_res = client.get("/api/v1/backup/latest")
    assert download_res.status_code == 200
    assert download_res.headers["cache-control"] == "no-store"
    assert create_res.json()["filename"] in download_res.headers["content-disposition"]

    downloaded = tmp_path / "downloaded.db"
    downloaded.write_bytes(download_res.content)
    conn = sqlite3.connect(str(downloaded))
    rows = conn.execute("SELECT content FROM notes").fetchall()
    assert ("hello backup",) in rows


def test_two_creates_in_a_row_both_succeed(client):
    # Filenames carry second-resolution timestamps, so two creates within the
    # same second legitimately produce the same filename (refreshing that
    # snapshot in place) rather than two distinct ones — assert only on what's
    # actually guaranteed: both calls succeed, and there's a downloadable
    # backup afterward.
    first = client.post("/api/v1/backup")
    second = client.post("/api/v1/backup")
    assert first.status_code == 200
    assert second.status_code == 200
    assert client.get("/api/v1/backup/latest").status_code == 200


def test_concurrent_backups_do_not_race():
    # create_backup_now() serializes on db._migration_lock specifically so that
    # concurrent calls can't collide on the same second-resolution staging path
    # mid-rename or mid-prune. Without that lock this reliably raises (or
    # silently loses a backup) under real thread concurrency.
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: db.create_backup_now(), range(8)))

    assert all(path is not None for path in results)
    assert db.latest_backup_path() is not None
