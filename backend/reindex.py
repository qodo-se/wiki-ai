import sys
import threading

from db import connect
from embeddings import EmbeddingError, embed_text
from vectorstore import VectorStoreError, delete_collection, upsert_vector

_reindex_lock = threading.Lock()


class ReindexInProgress(Exception):
    pass


def reindex_notes() -> dict:
    if not _reindex_lock.acquire(blocking=False):
        raise ReindexInProgress("a reindex run is already in progress")
    try:
        with connect() as db:
            rows = db.execute("SELECT id, content FROM notes").fetchall()

        # Embed everything before touching the existing collection. If the
        # embedding service is entirely unreachable, every embed below fails and
        # we bail out without deleting anything — a down Ollama must not swap a
        # working index for an empty one. A few isolated per-note failures still
        # proceed to the delete+rebuild, since those notes wouldn't have been
        # searchable anyway.
        embeddings, failed = [], 0
        for note_id, content in rows:
            try:
                embeddings.append((note_id, embed_text(content)))
            except EmbeddingError as e:
                print(f"warning: failed to embed note {note_id}: {e}", file=sys.stderr)
                failed += 1

        if rows and not embeddings:
            raise EmbeddingError(
                f"failed to embed any of {len(rows)} note(s) — embedding service "
                f"appears unreachable, leaving the existing index untouched"
            )

        # Wiped only now, and up front of the rebuild rather than upserted-over:
        # this is also how you recover from stale vectors left behind by notes
        # deleted before a restore (see docs/UPGRADING.md), which upserting alone
        # would never clear out.
        delete_collection()

        embedded = 0
        for note_id, vector in embeddings:
            try:
                upsert_vector(note_id, vector)
            except VectorStoreError as e:
                print(f"warning: failed to upsert vector for note {note_id}: {e}", file=sys.stderr)
                failed += 1
            else:
                embedded += 1

        return {"total": len(rows), "embedded": embedded, "failed": failed}
    finally:
        _reindex_lock.release()
