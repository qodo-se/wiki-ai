import sys
import threading

from chunking import chunk_content, chunk_point_id
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
        # searchable anyway. A note's chunks are embedded as a unit: one failing
        # chunk fails the whole note, same as "this note isn't fully searchable".
        note_vectors, failed = [], 0
        for note_id, content in rows:
            try:
                vectors = [embed_text(chunk) for chunk in chunk_content(content)]
            except EmbeddingError as e:
                print(f"warning: failed to embed note {note_id}: {e}", file=sys.stderr)
                failed += 1
                continue
            note_vectors.append((note_id, vectors))

        if rows and not note_vectors:
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
        for note_id, vectors in note_vectors:
            try:
                for i, vector in enumerate(vectors):
                    upsert_vector(chunk_point_id(note_id, i), vector, payload={"note_id": note_id})
            except VectorStoreError as e:
                print(f"warning: failed to upsert vectors for note {note_id}: {e}", file=sys.stderr)
                failed += 1
            else:
                embedded += 1

        return {"total": len(rows), "embedded": embedded, "failed": failed}
    finally:
        _reindex_lock.release()
