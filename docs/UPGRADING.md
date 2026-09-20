# Upgrading

This app stores your data in two places, both persisted in Docker named volumes so
they survive `docker compose pull && docker compose up -d`:

- **`wiki-data`** — a SQLite database (`/data/wiki.db`) holding every note and setting,
  plus an `/data/images` directory holding the bytes of every image uploaded into a
  note. This is the data that matters; back it up before any upgrade you're unsure about.
- **`qdrant-data`** — the semantic search vector index. It's derived data: if it's ever
  lost or out of date, `POST /api/v1/search/reindex` rebuilds it from SQLite. It does
  not need to be backed up.

## What happens automatically

On startup, the backend checks the SQLite database's schema version against what the
running code expects (`backend/db.py`). If there are new migrations to apply, it:

1. Takes a full backup of the database to `/data/backups/wiki-v<old-version>-<timestamp>.db`
   (inside the `wiki-data` volume) before changing anything.
2. Applies the pending migrations in one transaction — either all of them succeed, or
   none do.
3. Keeps the last 5 pre-migration backups and prunes older ones automatically.

If the database's schema version is *newer* than what the running code understands
(e.g. you rolled back to an older image after a newer one already migrated the data),
the app refuses to start rather than risk operating on a shape it doesn't recognize.

## Version policy

Releases follow semver (`MAJOR.MINOR.PATCH`, tracked in `backend/VERSION`). The
version a running instance was built from is queryable at `GET /api/v1/version` and
shown at the bottom of the settings page:

- **PATCH** — no schema change.
- **MINOR** — additive schema change only (new table/column).
- **MAJOR** — a schema change that reshapes or removes existing data. Where possible
  this is done as an *expand/contract*: one release adds the new shape alongside the
  old one (dual-write), a later release drops the old shape once it's no longer
  needed. Release notes call out when a MAJOR migration is not reversible.

**Rolling the app back is never automatic once a migration has run, regardless of
PATCH/MINOR/MAJOR.** The startup guard in `backend/db.py` deliberately refuses to
start any build against a database whose recorded schema version is newer than that
build understands — including a MINOR release's purely additive migration — so that
old code never silently operates on a shape it doesn't recognize. To roll back after a
migration has applied, restore the pre-migration backup that was taken automatically
(see below) before starting the older image.

## Manual backup

The automatic pre-migration backup covers upgrades, but nothing stops you from taking
your own snapshot beforehand. The API image doesn't include the `sqlite3` CLI, so use
Python's standard-library `sqlite3` module instead (also handles `/data/backups/` not
existing yet on an install that has never migrated):

```sh
docker compose exec api python -c "
import os, sqlite3
os.makedirs('/data/backups', exist_ok=True)
sqlite3.connect('/data/wiki.db').backup(sqlite3.connect('/data/backups/manual-$(date +%Y%m%d%H%M%S).db'))
"
docker cp $(docker compose ps -q api):/data/backups/manual-<timestamp>.db ./wiki-backup.db
```

(the timestamp includes time-of-day, not just the date, so running this twice in one
day can't silently overwrite an earlier snapshot)

This snippet only backs up the database — it does not include `/data/images`. For a
single backup that covers both, use the app's built-in on-demand backup instead
(**Settings → Backup**, or `wiki-cli backup`), which downloads a zip containing both
`wiki.db` and `images/`.

## Restoring a backup

If you're restoring the **built-in on-demand backup** (Settings → Backup, or `wiki-cli
backup`), it downloads as a **zip** containing `wiki.db` plus an `images/` directory —
extract it on the host first, before anything below:
```sh
unzip wiki-backup.zip -d wiki-backup-extracted
```
Wherever the steps below say "the backup file", use `wiki-backup-extracted/wiki.db` —
**never copy the zip itself over `/data/wiki.db`**, or you'll overwrite the live
database with zip bytes instead of the SQLite data inside it. A pre-migration snapshot
under `/data/backups/wiki-v*.db`, or the manual `sqlite3` snippet's output further up,
is already a raw database file — skip the unzip step for those.

1. If you're restoring a copy saved to the host (via `docker cp`, above, or the
   extracted `wiki.db` from a downloaded backup zip) rather than one already under
   `/data/backups/` in the `wiki-data` volume, copy it back in **first, before
   stopping the app** — `docker compose cp` targets the running `api` container,
   which the next step removes. Copy it to `/data/` directly rather than
   `/data/backups/`, since that subdirectory only gets created the first time a
   migration actually runs and may not exist yet on a fresh or replacement volume.
   If you also extracted an `images/` directory from a backup zip, copy that in now
   too, for the same reason — everything that needs the still-running container has
   to happen before the next step:
   ```sh
   docker compose cp ./wiki-backup-extracted/wiki.db api:/data/restore-source.db
   docker compose cp ./wiki-backup-extracted/images api:/data/restore-images-source
   ```
   (drop the second line if you're restoring a database-only backup with no images)
2. Stop the app: `docker compose down`.
3. Move the restored files into place. Copy the database file over `/data/wiki.db`
   and remove any leftover WAL sidecar files (they reference the old file and aren't
   valid against the restored one — SQLite recreates them fresh); point `cp` at
   `/data/restore-source.db` if you copied it in above, or at its path under
   `/data/backups/` if it was already in the volume. If you copied an images
   directory in above, replace `/data/images` with it too — this all has to run as
   one `docker compose run` (a one-off container over the stopped volume), since
   `docker compose cp` only works against an already-running container:
   ```sh
   docker compose run --rm --entrypoint sh api \
     -c "cp /data/restore-source.db /data/wiki.db && rm -f /data/wiki.db-wal /data/wiki.db-shm && rm -rf /data/images && mv /data/restore-images-source /data/images"
   ```
   (drop the `rm -rf /data/images && mv /data/restore-images-source /data/images`
   part of that command if you're restoring a database-only backup with no images —
   the `api` service already mounts the `wiki-data` volume at `/data`, so no extra
   `-v` is needed either way)
4. Start the app back up: `docker compose up -d`.
5. If semantic search results look stale afterward (the restored SQLite data no
   longer matches what's embedded in Qdrant), rebuild the index from the settings
   page (**Settings → Reindex search**) or directly:
   ```sh
   curl -X POST http://localhost:8081/api/v1/search/reindex
   ```
   This drops the Qdrant collection and re-embeds every note currently in SQLite, so
   it's always safe to re-run and leaves no stale vectors behind from notes that no
   longer exist. It responds `409` if a reindex is already running, and returns a
   count of notes embedded/failed once done — large wikis or a slow embedding model
   can take a while.

## If an upgrade fails partway

The migration transaction itself is all-or-nothing, so a failed migration leaves the
schema at its pre-upgrade version — the app will keep trying (and failing) the same
migration on every restart until the underlying issue is fixed or you roll back to the
previous image version. The pre-migration backup under `/data/backups/` is there as a
manual fallback if you'd rather restore and investigate offline.
