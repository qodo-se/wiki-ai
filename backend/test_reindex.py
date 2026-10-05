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
        reindex,
        "upsert_vectors",
        lambda points: calls.append(("upsert", points[0][2]["note_id"])),
    )

    result = reindex.reindex_notes()

    assert calls[0] == "delete"  # collection dropped only after embeddings succeed, before upserting
    assert ("upsert", "n1") in calls
    assert ("upsert", "n2") in calls
    assert result == {"total": 2, "embedded": 2, "failed": 0}


def test_reindex_chunks_each_note_into_multiple_points(monkeypatch):
    # "hello\n\nworld" is two paragraphs -> two chunks, so one note should
    # produce two upserted points (batched into one upsert_vectors call),
    # each tagged with the same note_id and its own chunk_index.
    _insert_note("n1", "hello\n\nworld")

    monkeypatch.setattr(reindex, "delete_collection", lambda: None)
    monkeypatch.setattr(reindex, "embed_text", lambda content: [0.1])
    calls = []
    monkeypatch.setattr(reindex, "upsert_vectors", lambda points: calls.append(points))

    result = reindex.reindex_notes()

    assert len(calls) == 1  # one batched call, not one per chunk
    points = calls[0]
    assert len(points) == 2
    assert {p[2]["note_id"] for p in points} == {"n1"}
    assert {p[2]["chunk_index"] for p in points} == {0, 1}
    assert len({p[0] for p in points}) == 2  # distinct point ids per chunk
    assert result == {"total": 1, "embedded": 1, "failed": 0}


def test_reindex_counts_per_note_failures_without_aborting(monkeypatch):
    _insert_note("n1", "hello")
    _insert_note("n2", "world")

    monkeypatch.setattr(reindex, "delete_collection", lambda: None)

    def fake_embed(content):
        if content == "hello":
            raise reindex.EmbeddingError("boom")
        return [0.1]

    monkeypatch.setattr(reindex, "embed_text", fake_embed)
    monkeypatch.setattr(reindex, "upsert_vectors", lambda points: None)

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


def test_reindex_aborts_even_when_a_blank_note_coexists_with_a_fully_down_service(monkeypatch):
    # A blank note has zero chunks, so it "succeeds" without ever calling
    # embed_text — that must not mask every real note failing and let the
    # abort-on-total-failure check slip through.
    _insert_note("blank", "")
    _insert_note("n1", "hello")

    delete_called = []
    monkeypatch.setattr(reindex, "delete_collection", lambda: delete_called.append(True))

    def fake_embed(content):
        raise reindex.EmbeddingError("connection refused")

    monkeypatch.setattr(reindex, "embed_text", fake_embed)

    with pytest.raises(reindex.EmbeddingError):
        reindex.reindex_notes()

    assert not delete_called


def test_reindex_proceeds_when_every_note_is_blank(monkeypatch):
    # Nothing real to lose — an all-blank wiki shouldn't trip the abort check
    # just because embed_text was never called.
    _insert_note("blank1", "")
    _insert_note("blank2", "   ")

    monkeypatch.setattr(reindex, "delete_collection", lambda: None)
    monkeypatch.setattr(reindex, "embed_text", lambda content: (_ for _ in ()).throw(AssertionError("should not be called")))
    monkeypatch.setattr(reindex, "upsert_vectors", lambda points: None)

    result = reindex.reindex_notes()

    assert result == {"total": 2, "embedded": 2, "failed": 0}


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
