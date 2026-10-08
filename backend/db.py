import datetime
import glob
import os
import sqlite3
import sys
import threading
import zipfile

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

CREATE TABLE IF NOT EXISTS embedding_index_state (
    version INTEGER NOT NULL
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
    # Metadata for images attached to a note. The bytes themselves live on disk
    # under images_dir(), keyed by id — never in this table.
    (2, [
        """
        CREATE TABLE images (
            id         TEXT PRIMARY KEY,
            note_id    TEXT NOT NULL,
            filename   TEXT NOT NULL,
            mime_type  TEXT NOT NULL,
            size       INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
        "CREATE INDEX idx_images_note_id ON images (note_id);",
    ]),
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


def images_dir() -> str:
    # A function (not a module-level constant) for the same reason as
    # _backup_dir(): it must reflect DB_PATH as monkeypatched by tests, not
    # whatever DB_PATH was at import time.
    return os.path.join(os.path.dirname(DB_PATH) or ".", "images")


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


# The on-demand manual backup is a single fixed file, always overwritten — no
# history, no timestamps, nothing to prune. Deliberately separate from the
# automatic pre-migration snapshots above (different lock, different naming):
# those exist to protect a schema upgrade specifically, this exists so a
# person can click one button and get one file. Keeping them independent
# means neither has to reason about the other's locking or retention.
#
# It's a zip (a DB snapshot plus every note image) rather than a bare .db file
# so a single download is a complete backup now that image bytes live outside
# the database.
MANUAL_BACKUP_FILENAME = "manual-backup.zip"
_manual_backup_lock = threading.Lock()


def manual_backup_path() -> str:
    return os.path.join(_backup_dir(), MANUAL_BACKUP_FILENAME)


def try_create_manual_backup() -> bool:
    """Overwrites the single manual backup zip (DB snapshot + images) with a
    fresh one. Returns False immediately, without blocking, if a backup is
    already in progress — callers should treat that as "try again shortly",
    not queue behind it."""
    if not _manual_backup_lock.acquire(blocking=False):
        return False
    try:
        os.makedirs(_backup_dir(), exist_ok=True)
        dest_path = manual_backup_path()
        # Staging file + rename so a backup that fails partway through can't
        # leave a truncated file where a good one previously was.
        staging_path = dest_path + ".tmp"
        staging_db_path = dest_path + ".db.tmp"
        source = sqlite3.connect(DB_PATH, timeout=CONNECT_TIMEOUT_SECONDS)
        try:
            dest = sqlite3.connect(staging_db_path, timeout=CONNECT_TIMEOUT_SECONDS)
            try:
                source.backup(dest)  # safe under WAL, unlike a raw file copy
            finally:
                dest.close()
            with zipfile.ZipFile(staging_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(staging_db_path, "wiki.db")
                images_path = images_dir()
                if os.path.isdir(images_path):
                    for name in os.listdir(images_path):
                        zf.write(os.path.join(images_path, name), os.path.join("images", name))
        except Exception:
            if os.path.exists(staging_path):
                os.remove(staging_path)
            raise
        finally:
            source.close()
            if os.path.exists(staging_db_path):
                os.remove(staging_db_path)
        os.rename(staging_path, dest_path)
        return True
    finally:
        _manual_backup_lock.release()


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


# Bump this whenever the embedding/chunking format changes (what gets embedded, or
# how a note is split into vectors) — not for every release. Unlike MIGRATIONS, this
# isn't an ordered log: it's a single current/stale comparison, since a format change
# always means "throw out every vector and re-embed everything", never an incremental
# step. A mismatch (or no stored version at all, e.g. upgrading from before this
# existed) triggers a full reindex the next time connect() runs, so existing notes
# don't silently fall out of semantic/hybrid search waiting on someone to notice
# docs/UPGRADING.md and run one by hand.
EMBEDDING_INDEX_VERSION = 1


def _embedding_index_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM embedding_index_state").fetchone()
    return row[0] if row else 0


def _mark_embedding_index_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("DELETE FROM embedding_index_state")
    conn.execute("INSERT INTO embedding_index_state (version) VALUES (?)", (version,))
    conn.commit()


# connect() isn't just a startup call — every request that touches the database calls
# it again — so the version check below must run at most once per process, not once
# per connect() call. Without this, a down Ollama/Qdrant would turn every single
# request (note reads, config, unrelated to search) into another corpus-wide reindex
# attempt for as long as the outage lasts, instead of actually waiting for a restart
# the way the retry messages below claim.
_embedding_reindex_checked = False
_embedding_reindex_check_lock = threading.Lock()


def _reindex_if_embedding_format_changed(conn: sqlite3.Connection) -> None:
    # Deliberately runs outside the `with _migration_lock:` block in connect(): it may
    # call reindex_notes(), which itself calls connect() to read notes, and the lock
    # above is a plain (non-reentrant) threading.Lock — holding it here would deadlock
    # that nested call. Also keeps a slow, network-dependent reindex out of the
    # schema-migration transaction's critical section.
    global _embedding_reindex_checked
    with _embedding_reindex_check_lock:
        if _embedding_reindex_checked:
            return
        # Set before doing any of the work below (not after): this is also what
        # keeps reindex_notes()'s own nested `with connect() as db:` call from
        # recursing back into this function — by the time that nested connect()
        # runs, this flag is already set, so it returns immediately instead of
        # trying to start a second concurrent reindex.
        _embedding_reindex_checked = True

    # Equality, not >=: unlike schema_migrations this isn't an ordered log where
    # "ahead" is always safe, it's a single current/stale comparison (see
    # EMBEDDING_INDEX_VERSION above) — a stored version from a *newer* build (e.g.
    # after rolling back to this one) means the current code's embedding/chunking
    # logic doesn't match what's actually in Qdrant either, so it needs the same
    # full reindex as being behind does.
    if _embedding_index_version(conn) == EMBEDDING_INDEX_VERSION:
        return

    note_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    if note_count == 0:
        # Nothing to embed yet — mark current without requiring Ollama/Qdrant to be
        # reachable just to boot a brand-new, empty wiki (or run the test suite,
        # which creates a fresh empty database on every test).
        _mark_embedding_index_version(conn, EMBEDDING_INDEX_VERSION)
        return

    # Lazy import: reindex.py imports connect() from this module.
    import reindex

    try:
        result = reindex.reindex_notes()
    except (reindex.EmbeddingError, reindex.VectorStoreError) as e:
        # Same principle this app already applies when saving a note: a down
        # embedding service should never block using the rest of the wiki. Leave
        # the stored version unset so this retries on the next process restart.
        print(
            f"warning: automatic reindex to embedding format v{EMBEDDING_INDEX_VERSION} "
            f"failed, will retry on next startup: {e}",
            file=sys.stderr,
        )
        return

    if result["failed"]:
        # A format-version reindex re-embeds every note, so a note that failed here
        # isn't some pre-existing, already-known gap — leave the version unmarked so
        # a future restart retries it, rather than silently settling for an
        # incomplete index until the next format bump.
        print(
            f"warning: automatic reindex to embedding format v{EMBEDDING_INDEX_VERSION} "
            f"completed with {result['failed']} of {result['total']} note(s) failing, "
            f"will retry on next startup",
            file=sys.stderr,
        )
        return

    _mark_embedding_index_version(conn, EMBEDDING_INDEX_VERSION)


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
        _reindex_if_embedding_format_changed(conn)
    except Exception:
        conn.close()
        raise
    return conn
