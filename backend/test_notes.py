import pytest
from pydantic import ValidationError

import routes.notes as notes_module
from routes.notes import NoteBody, _sync_embedding


def test_sync_embedding_upserts_chunks_then_cleans_up_stale_ones(monkeypatch):
    calls = []
    monkeypatch.setattr(notes_module, "embed_text", lambda chunk: [0.1])
    monkeypatch.setattr(notes_module, "upsert_vectors", lambda points: calls.append(("upsert", points)))
    monkeypatch.setattr(
        notes_module, "delete_stale_chunks", lambda note_id, keep_count: calls.append(("cleanup", note_id, keep_count))
    )

    _sync_embedding("n1", "first paragraph\n\nsecond paragraph")

    assert calls[0][0] == "upsert"  # new chunks land before stale ones are cleaned up
    points = calls[0][1]
    assert len(points) == 2
    assert {p[2]["chunk_index"] for p in points} == {0, 1}
    assert calls[1] == ("cleanup", "n1", 2)  # keep exactly the 2 chunks just upserted


def test_sync_embedding_skips_upsert_and_cleanup_on_embedding_failure(monkeypatch):
    calls = []

    def fake_embed(chunk):
        raise notes_module.EmbeddingError("ollama down")

    monkeypatch.setattr(notes_module, "embed_text", fake_embed)
    monkeypatch.setattr(notes_module, "upsert_vectors", lambda points: calls.append("upsert"))
    monkeypatch.setattr(notes_module, "delete_stale_chunks", lambda note_id, keep_count: calls.append("cleanup"))

    _sync_embedding("n1", "some content")  # must not raise — best-effort

    assert calls == []  # a note's existing vectors must be left untouched


def test_sync_embedding_skips_cleanup_when_upsert_fails(monkeypatch):
    # If the upsert fails partway, stale chunks from the note's previous
    # version must NOT be deleted — they're still the only valid vectors it
    # has, since the replacement never fully landed.
    calls = []
    monkeypatch.setattr(notes_module, "embed_text", lambda chunk: [0.1])

    def fake_upsert(points):
        raise notes_module.VectorStoreError("dimension mismatch")

    monkeypatch.setattr(notes_module, "upsert_vectors", fake_upsert)
    monkeypatch.setattr(notes_module, "delete_stale_chunks", lambda note_id, keep_count: calls.append("cleanup"))

    _sync_embedding("n1", "some content")  # must not raise — best-effort

    assert calls == []


def test_note_body_normalizes_path_on_construction():
    assert NoteBody(content="x", path="/recipe/").path == "/recipe"


def test_equivalent_paths_normalize_to_the_same_string():
    a = NoteBody(content="x", path="/recipe/")
    b = NoteBody(content="x", path="recipe ")
    c = NoteBody(content="x", path="//recipe//")
    assert a.path == b.path == c.path == "/recipe"


def test_note_body_defaults_to_root_path():
    assert NoteBody(content="x").path == "/"


def test_note_body_still_rejects_disallowed_characters():
    with pytest.raises(ValidationError):
        NoteBody(content="x", path="/recipe;drop")
