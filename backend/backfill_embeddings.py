"""One-off: embed every existing note into Qdrant.

Run inside the api container after configuring Ollama/Qdrant, e.g.:
    docker compose exec api python backfill_embeddings.py
"""

import sys

from db import connect
from embeddings import EmbeddingError, embed_text
from vectorstore import VectorStoreError, upsert_vector


def main() -> None:
    with connect() as db:
        rows = db.execute("SELECT id, content FROM notes").fetchall()

    print(f"embedding {len(rows)} note(s)...")
    failed = 0
    for note_id, content in rows:
        try:
            vector = embed_text(content)
            upsert_vector(note_id, vector)
        except (EmbeddingError, VectorStoreError) as e:
            print(f"  failed: {note_id}: {e}", file=sys.stderr)
            failed += 1
        else:
            print(f"  embedded: {note_id}")

    print(f"done — {len(rows) - failed} succeeded, {failed} failed")


if __name__ == "__main__":
    main()
