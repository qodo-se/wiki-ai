import db
import search


def _insert_note(note_id, content, path="/", title=""):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO notes (id, content, path, title) VALUES (?, ?, ?, ?)",
            (note_id, content, path, title),
        )


def test_semantic_search_empty_query_short_circuits(monkeypatch):
    called = False

    def fake_embed(q):
        nonlocal called
        called = True
        return [0.1]

    monkeypatch.setattr(search, "embed_text", fake_embed)
    assert search.semantic_search_notes("   ", 10) == []
    assert not called


def test_semantic_search_joins_back_to_sqlite(monkeypatch):
    _insert_note("note-1", "shampoo and conditioner", path="/hair", title="Hair")
    monkeypatch.setattr(search, "embed_text", lambda q: [0.1, 0.2])
    monkeypatch.setattr(
        search,
        "search_vectors",
        lambda vector, limit: [{"id": "note-1", "score": 0.93, "payload": {}}],
    )

    hits = search.semantic_search_notes("hair products", 10)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.id == "note-1"
    assert hit.title == "Hair"
    assert hit.path == "/hair"
    assert hit.score == 0.93


def test_semantic_search_skips_orphaned_vector(monkeypatch):
    # Qdrant still has a vector for a note that's since been deleted from
    # sqlite — must be dropped, not surfaced as a hit with no content.
    monkeypatch.setattr(search, "embed_text", lambda q: [0.1])
    monkeypatch.setattr(
        search,
        "search_vectors",
        lambda vector, limit: [{"id": "deleted-note", "score": 0.5, "payload": {}}],
    )
    assert search.semantic_search_notes("anything", 10) == []
