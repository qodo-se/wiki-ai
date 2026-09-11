import pytest

import db
import reindex


def _insert_note(note_id, content):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO notes (id, content) VALUES (?, ?)", (note_id, content)
        )


def test_reindex_deletes_collection_then_reembeds_every_note(monkeypatch):
    _insert_note("n1", "hello")
    _insert_note("n2", "world")

    calls = []
    monkeypatch.setattr(reindex, "delete_collection", lambda: calls.append("delete"))
    monkeypatch.setattr(reindex, "embed_text", lambda content: [0.1])
    monkeypatch.setattr(
        reindex, "upsert_vector", lambda note_id, vector: calls.append(("upsert", note_id))
    )

    result = reindex.reindex_notes()

    assert calls[0] == "delete"  # collection dropped only after embeddings succeed, before upserting
    assert ("upsert", "n1") in calls
    assert ("upsert", "n2") in calls
    assert result == {"total": 2, "embedded": 2, "failed": 0}


def test_reindex_counts_per_note_failures_without_aborting(monkeypatch):
    _insert_note("n1", "hello")
    _insert_note("n2", "world")

    monkeypatch.setattr(reindex, "delete_collection", lambda: None)

    def fake_embed(content):
        if content == "hello":
            raise reindex.EmbeddingError("boom")
        return [0.1]

    monkeypatch.setattr(reindex, "embed_text", fake_embed)
    monkeypatch.setattr(reindex, "upsert_vector", lambda note_id, vector: None)

    result = reindex.reindex_notes()

    assert result == {"total": 2, "embedded": 1, "failed": 1}


def test_reindex_aborts_without_deleting_when_embedding_service_is_entirely_down(monkeypatch):
    # A fully unreachable Ollama must not swap a working index for an empty one.
    _insert_note("n1", "hello")
    _insert_note("n2", "world")

    delete_called = []
    monkeypatch.setattr(reindex, "delete_collection", lambda: delete_called.append(True))

    def fake_embed(content):
        raise reindex.EmbeddingError("connection refused")

    monkeypatch.setattr(reindex, "embed_text", fake_embed)

    with pytest.raises(reindex.EmbeddingError):
        reindex.reindex_notes()

    assert not delete_called


def test_reindex_rejects_concurrent_calls():
    reindex._reindex_lock.acquire()
    try:
        with pytest.raises(reindex.ReindexInProgress):
            reindex.reindex_notes()
    finally:
        reindex._reindex_lock.release()


def test_reindex_releases_lock_even_if_delete_collection_fails(monkeypatch):
    def fake_delete():
        raise reindex.VectorStoreError("unreachable")

    monkeypatch.setattr(reindex, "delete_collection", fake_delete)

    with pytest.raises(reindex.VectorStoreError):
        reindex.reindex_notes()

    assert reindex._reindex_lock.acquire(blocking=False)
    reindex._reindex_lock.release()
