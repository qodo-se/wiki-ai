import glob
import os
import sqlite3

import pytest

import db


def _column_names(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_db_starts_at_version_zero_with_no_migrations(monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [])
    conn = db.connect()
    assert db._current_version(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0


def test_pending_migration_is_applied_and_recorded(monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [])
    db.connect()  # baseline install, nothing pending yet
    monkeypatch.setattr(
        db,
        "MIGRATIONS",
        [(1, ["ALTER TABLE notes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"])],
    )
    conn = db.connect()
    assert "archived" in _column_names(conn, "notes")
    assert db._current_version(conn) == 1


def test_migration_is_idempotent_once_applied(monkeypatch):
    migrations = [(1, ["ALTER TABLE notes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"])]
    monkeypatch.setattr(db, "MIGRATIONS", migrations)
    db.connect()
    conn = db.connect()  # must not try to re-add the column
    assert db._current_version(conn) == 1


def test_backup_created_before_migration_runs_against_existing_data(monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [])
    conn = db.connect()
    conn.execute(
        "INSERT INTO notes (id, content, title) VALUES ('n1', 'hello', 'Note')"
    )
    conn.commit()

    monkeypatch.setattr(
        db,
        "MIGRATIONS",
        [(1, ["ALTER TABLE notes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"])],
    )
    conn = db.connect()

    backups = glob.glob(os.path.join(db._backup_dir(), "wiki-v0-*.db"))
    assert len(backups) == 1
    row = conn.execute("SELECT content, title FROM notes WHERE id = 'n1'").fetchone()
    assert row == ("hello", "Note")


def test_backup_before_migration_also_copies_images_dir(monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [])
    conn = db.connect()
    conn.execute("INSERT INTO notes (id, content) VALUES ('n1', 'hello')")
    conn.commit()
    conn.close()

    os.makedirs(db.IMAGES_DIR)
    with open(os.path.join(db.IMAGES_DIR, "abc.png"), "wb") as f:
        f.write(b"fake-bytes")

    monkeypatch.setattr(
        db, "MIGRATIONS", [(1, ["ALTER TABLE notes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"])]
    )
    db.connect()

    image_backups = glob.glob(os.path.join(db._backup_dir(), "images-v0-*"))
    assert len(image_backups) == 1
    with open(os.path.join(image_backups[0], "abc.png"), "rb") as f:
        assert f.read() == b"fake-bytes"


def test_no_images_backup_when_images_dir_does_not_exist(monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [])
    conn = db.connect()
    conn.execute("INSERT INTO notes (id, content) VALUES ('n1', 'hello')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        db, "MIGRATIONS", [(1, ["ALTER TABLE notes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"])]
    )
    db.connect()

    assert glob.glob(os.path.join(db._backup_dir(), "images-v*")) == []


def test_image_backup_retention_keeps_only_last_n(monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [])
    db.connect()
    os.makedirs(db.IMAGES_DIR, exist_ok=True)
    for version in range(1, db.BACKUP_RETENTION + 3):
        monkeypatch.setattr(
            db, "MIGRATIONS", [(v, ["SELECT 1"]) for v in range(1, version + 1)]
        )
        db.connect()

    image_backups = glob.glob(os.path.join(db._backup_dir(), "images-v*"))
    assert len(image_backups) == db.BACKUP_RETENTION


def test_no_backup_on_fresh_install(monkeypatch):
    monkeypatch.setattr(
        db,
        "MIGRATIONS",
        [(1, ["ALTER TABLE notes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"])],
    )
    db.connect()  # first-ever connect: nothing to back up
    assert not os.path.exists(db._backup_dir())


def test_backup_retention_keeps_only_last_n(monkeypatch):
    # Baseline install at version 0, regardless of how many real migrations
    # exist — the loop below establishes its own synthetic version history
    # starting from 1, so the real MIGRATIONS list must not race ahead of it.
    monkeypatch.setattr(db, "MIGRATIONS", [])
    db.connect()
    for version in range(1, db.BACKUP_RETENTION + 3):
        # MIGRATIONS is always the full cumulative history in production (a new
        # release ships the complete list, not just its own new entry) — match
        # that here rather than replacing it with a single out-of-sequence tuple.
        monkeypatch.setattr(
            db, "MIGRATIONS", [(v, ["SELECT 1"]) for v in range(1, version + 1)]
        )
        db.connect()

    backups = glob.glob(os.path.join(db._backup_dir(), "wiki-v*.db"))
    assert len(backups) == db.BACKUP_RETENTION


def test_backup_retention_sorts_by_mtime_not_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "BACKUP_RETENTION", 2)
    os.makedirs(db._backup_dir())
    # Unpadded version numbers sort "v10" before "v9" lexicographically, but v10
    # is the newer backup — retention must keep it based on recency, not name.
    old_path = os.path.join(db._backup_dir(), "wiki-v9-20260101T000000Z.db")
    new_path = os.path.join(db._backup_dir(), "wiki-v10-20260102T000000Z.db")
    for path, age in [(old_path, 20), (new_path, 10)]:
        open(path, "w").close()
        stamp = os.path.getmtime(path) - age
        os.utime(path, (stamp, stamp))

    db._prune_old_backups()

    remaining = set(glob.glob(os.path.join(db._backup_dir(), "wiki-v*.db")))
    assert remaining == {old_path, new_path}  # both fit within retention of 2

    monkeypatch.setattr(db, "BACKUP_RETENTION", 1)
    db._prune_old_backups()
    remaining = glob.glob(os.path.join(db._backup_dir(), "wiki-v*.db"))
    assert remaining == [new_path]


def test_failed_backup_cleans_up_staging_file(monkeypatch):
    # sqlite3.Connection is an immutable C type, so its methods can't be monkeypatched
    # directly. Instead, intercept sqlite3.connect() for the staging path only: create
    # the file for real (simulating a partially-written backup), then hand back a
    # stand-in whose type mismatch makes the real Connection.backup() raise.
    monkeypatch.setattr(db, "MIGRATIONS", [])
    conn = db.connect()
    conn.execute("INSERT INTO notes (id, content, title) VALUES ('n1', 'hello', 'Note')")
    conn.commit()

    real_connect = sqlite3.connect

    class _FakeDest:
        def close(self):
            pass

    def _fake_connect(path, *args, **kwargs):
        if str(path).endswith(".tmp"):
            real_connect(path, *args, **kwargs).close()
            return _FakeDest()
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _fake_connect)
    monkeypatch.setattr(db, "MIGRATIONS", [(1, ["SELECT 1"])])

    with pytest.raises(Exception):
        db.connect()

    assert glob.glob(os.path.join(db._backup_dir(), "wiki-v*")) == []


def test_migrations_must_be_sequential_from_one(monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [(2, ["SELECT 1"])])  # gap: missing 1
    with pytest.raises(AssertionError):
        db.connect()


def test_migrations_must_not_have_duplicates(monkeypatch):
    monkeypatch.setattr(
        db, "MIGRATIONS", [(1, ["SELECT 1"]), (1, ["SELECT 1"])]
    )
    with pytest.raises(AssertionError):
        db.connect()


def test_corrupt_migration_history_raises_assertion_error(monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [])
    conn = db.connect()
    conn.execute("INSERT INTO schema_migrations (version) VALUES (1)")
    conn.execute("INSERT INTO schema_migrations (version) VALUES (3)")  # gap: missing 2
    conn.commit()
    conn.close()

    with pytest.raises(AssertionError):
        db.connect()


def test_downgrade_raises_schema_too_new_error(monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [])
    conn = db.connect()
    conn.execute("INSERT INTO schema_migrations (version) VALUES (1)")
    conn.commit()
    conn.close()

    with pytest.raises(db.SchemaTooNewError):
        db.connect()


def test_path_normalization_migration_collapses_equivalent_paths(monkeypatch):
    real_migrations = list(db.MIGRATIONS)  # restored below, without touching temp_db's DB_PATH patch
    monkeypatch.setattr(db, "MIGRATIONS", [])
    conn = db.connect()
    conn.executemany(
        "INSERT INTO notes (id, content, path) VALUES (?, ?, ?)",
        [
            ("n1", "a", "/recipe"),
            ("n2", "b", "/recipe/"),
            ("n3", "c", "/recipe "),
            ("n4", "d", "recipe"),
            ("n5", "e", "//recipe//"),
            # a slash run deeper than a handful of fixed collapse passes could fix,
            # and internal segment whitespace — both regressions caught in review of
            # an earlier version of this migration that used bounded SQL REPLACEs
            ("n6", "f", "////////recipe"),
            ("n7", "g", "/recipe/ thai "),
            ("n8", "h", "/recipe/thai"),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "MIGRATIONS", real_migrations)
    conn = db.connect()
    rows = dict(conn.execute("SELECT id, path FROM notes ORDER BY id"))
    assert rows == {
        "n1": "/recipe",
        "n2": "/recipe",
        "n3": "/recipe",
        "n4": "/recipe",
        "n5": "/recipe",
        "n6": "/recipe",
        "n7": "/recipe/thai",
        "n8": "/recipe/thai",
    }


def test_images_to_filesystem_migration_moves_bytes_and_drops_column(monkeypatch):
    real_migrations = list(db.MIGRATIONS)  # restored below, without touching temp_db's DB_PATH patch
    monkeypatch.setattr(db, "MIGRATIONS", [])
    conn = db.connect()
    conn.execute("INSERT INTO notes (id, content) VALUES ('n1', 'hello')")
    # BASE_SCHEMA now creates images without a data column (a fresh install never
    # sees the old shape), so simulate an install that predates this migration by
    # recreating the table in its old, BLOB-backed shape before inserting into it.
    conn.execute("DROP TABLE images")
    conn.execute(
        """
        CREATE TABLE images (
            id TEXT PRIMARY KEY, note_id TEXT NOT NULL, filename TEXT NOT NULL DEFAULT '',
            mime_type TEXT NOT NULL, size INTEGER NOT NULL, data BLOB NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "INSERT INTO images (id, note_id, filename, mime_type, size, data) "
        "VALUES ('img1', 'n1', 'pixel.png', 'image/png', 4, ?)",
        (b"fake",),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "MIGRATIONS", real_migrations)
    conn = db.connect()

    assert "data" not in _column_names(conn, "images")
    row = conn.execute(
        "SELECT note_id, filename, mime_type, size FROM images WHERE id = 'img1'"
    ).fetchone()
    assert row == ("n1", "pixel.png", "image/png", 4)

    with open(db.image_path("img1", "image/png"), "rb") as f:
        assert f.read() == b"fake"


def test_failed_migration_rolls_back_partial_changes(monkeypatch):
    monkeypatch.setattr(
        db,
        "MIGRATIONS",
        [
            (
                1,
                [
                    "ALTER TABLE notes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
                    "THIS IS NOT VALID SQL",
                ],
            )
        ],
    )
    with pytest.raises(Exception):
        db.connect()

    # Open the file directly — db.connect() would just hit the same failing
    # migration again since it never got recorded as applied.
    raw = sqlite3.connect(db.DB_PATH, timeout=db.CONNECT_TIMEOUT_SECONDS)
    assert "archived" not in _column_names(raw, "notes")
    assert db._current_version(raw) == 0
