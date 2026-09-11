import datetime
import glob
import os
import sqlite3
import threading

DB_PATH = "/data/wiki.db"
BACKUP_RETENTION = 5
CONNECT_TIMEOUT_SECONDS = 30.0

# Serializes migration application across threads within this process. It does not
# cover multiple processes/containers sharing the same DB file, which is fine given
# the deployment this app ships with: the Dockerfile runs a single uvicorn process
# (no --workers), and docker-compose runs a single api replica.
_migration_lock = threading.Lock()

# Created before the downgrade guard runs, so the guard can be checked before any
# other schema statement executes — a rollback past a migration that dropped a table
# must not let BASE_SCHEMA's CREATE TABLE IF NOT EXISTS resurrect it first.
BOOTSTRAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id         TEXT PRIMARY KEY,
    content    TEXT NOT NULL DEFAULT '',
    path       TEXT NOT NULL DEFAULT '/',
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

def _normalize_existing_note_paths(conn: sqlite3.Connection) -> None:
    # A migration step (rather than plain SQL statements) so this applies the exact
    # same normalize_path() used to validate new writes in routes.notes — a fixed
    # number of SQL REPLACE passes can't collapse arbitrarily long runs of slashes or
    # trim whitespace around individual segments, so it would leave some legacy paths
    # non-canonical. Imported lazily to avoid a module-load cycle (paths is imported
    # by routes.notes, which imports db).
    from paths import normalize_path

    rows = conn.execute("SELECT id, path FROM notes").fetchall()
    updates = [
        (normalized, note_id)
        for note_id, path in rows
        if (normalized := normalize_path(path)) != path
    ]
    if updates:
        conn.executemany("UPDATE notes SET path = ? WHERE id = ?", updates)


# BOOTSTRAP_SCHEMA/BASE_SCHEMA are never backed up before running: every statement in
# them is an idempotent `CREATE TABLE IF NOT EXISTS`, so they're safe-by-construction
# against any existing database and can't lose data. Only MIGRATIONS entries (which
# may ALTER or rewrite existing data) go through the backup-then-apply path below.
#
# Ordered, append-only list of (version, [statements]) applied after BASE_SCHEMA.
# Version 0 is the schema above (every install already has it). Add new migrations at
# the end with the next sequential version number; never edit or remove a released one
# — the version number is a durable record of what's already been applied to real
# user databases.
#
# Each migration's statements run individually — a plain string via conn.execute(),
# or a callable(conn) for a data fixup too irregular to express as a handful of SQL
# statements — so the whole migration commits or rolls back as one transaction.
MIGRATIONS: list[tuple[int, list]] = [
    # Canonicalizes existing note paths, so notes saved before that validation
    # existed (e.g. "/recipe/" or "/recipe " next to "/recipe") group correctly on
    # the by-path home view instead of appearing as separate paths.
    (1, [_normalize_existing_note_paths]),
]


class SchemaTooNewError(Exception):
    """The on-disk schema is newer than this build of the app understands."""


def _validate_migrations() -> None:
    expected = 1
    for version, _statements in MIGRATIONS:
        if version != expected:
            raise AssertionError(
                f"MIGRATIONS must be sequential starting at 1 with no gaps or "
                f"duplicates; expected version {expected} but found {version}"
            )
        expected += 1


def _backup_dir() -> str:
    return os.path.join(os.path.dirname(DB_PATH) or ".", "backups")


def _current_version(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    applied = [row[0] for row in rows]
    expected = list(range(1, len(applied) + 1))
    if applied != expected:
        raise AssertionError(
            f"schema_migrations recorded versions {applied}, which has gaps or "
            f"duplicates relative to the expected sequential {expected} — the "
            f"database's migration history is corrupt and must be fixed by hand "
            f"before this app can safely determine what's already applied."
        )
    return applied[-1] if applied else 0


def _prune_old_backups() -> None:
    # Sort by mtime, not filename — filenames embed an unpadded schema version, so
    # sorting lexicographically would misorder v10 before v9 once versions hit two
    # digits.
    backups = sorted(
        glob.glob(os.path.join(_backup_dir(), "wiki-v*.db")), key=os.path.getmtime
    )
    for stale in backups[:-BACKUP_RETENTION]:
        os.remove(stale)


def _backup_before_migration(current_version: int, db_existed: bool) -> None:
    if not db_existed:
        return  # fresh install, nothing to protect
    os.makedirs(_backup_dir(), exist_ok=True)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_path = os.path.join(_backup_dir(), f"wiki-v{current_version}-{timestamp}.db")
    # Back up to a staging path and rename into place only once it's complete, so a
    # backup that fails partway through can't leave a corrupt file under the
    # wiki-v*.db pattern that retention would later count as a valid snapshot.
    staging_path = dest_path + ".tmp"
    source = sqlite3.connect(DB_PATH, timeout=CONNECT_TIMEOUT_SECONDS)
    try:
        dest = sqlite3.connect(staging_path, timeout=CONNECT_TIMEOUT_SECONDS)
        try:
            source.backup(dest)  # safe under WAL, unlike a raw file copy
        finally:
            dest.close()
    except Exception:
        if os.path.exists(staging_path):
            os.remove(staging_path)
        raise
    finally:
        source.close()
    os.rename(staging_path, dest_path)
    _prune_old_backups()


def _check_not_too_new(conn: sqlite3.Connection) -> int:
    current = _current_version(conn)
    max_version = MIGRATIONS[-1][0] if MIGRATIONS else 0
    if current > max_version:
        raise SchemaTooNewError(
            f"database schema is at version {current}, but this build of the app "
            f"only understands up to version {max_version}. Refusing to start to "
            f"avoid operating on an unrecognized schema — upgrade the app instead "
            f"of downgrading it, or restore an older database backup."
        )
    return current


def _apply_migrations(conn: sqlite3.Connection, current: int, db_existed: bool) -> None:
    pending = [(version, statements) for version, statements in MIGRATIONS if version > current]
    if not pending:
        return
    _backup_before_migration(current, db_existed)
    try:
        conn.execute("BEGIN")
        for version, statements in pending:
            for statement in statements:
                if callable(statement):
                    statement(conn)
                else:
                    conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def connect() -> sqlite3.Connection:
    _validate_migrations()
    db_existed = os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH, timeout=CONNECT_TIMEOUT_SECONDS)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(BOOTSTRAP_SCHEMA)
        with _migration_lock:
            # Checked before BASE_SCHEMA runs: a rollback past a migration that
            # dropped a table must raise here, before CREATE TABLE IF NOT EXISTS gets
            # a chance to recreate anything the newer schema intentionally removed.
            current = _check_not_too_new(conn)
            conn.executescript(BASE_SCHEMA)
            _apply_migrations(conn, current, db_existed)
    except Exception:
        conn.close()
        raise
    return conn
