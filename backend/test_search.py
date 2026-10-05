import db
import search


def _insert_note(note_id, content, path="/", title=""):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO notes (id, content, path, title) VALUES (?, ?, ?, ?)",
            (note_id, content, path, title),
        )


def test_keyword_matches_matches_whole_words_not_substrings():
    # "car" must not match "career" — the bug that let keyword false
    # positives crowd out genuinely relevant notes in hybrid search.
    _insert_note("career", "Career Goals Notes", title="Career Goals Notes")
    _insert_note("car", "Car Maintenance Log", title="Car Maintenance Log")

    hits = search.keyword_matches("car")

    ids = [h.id for h in hits]
    assert ids == ["car"]


def test_keyword_matches_is_case_insensitive_and_counts_whole_word_occurrences():
    _insert_note("n1", "Car trouble again. My car won't start.", title="n1")

    hits = search.keyword_matches("CAR")

    assert len(hits) == 1
    assert hits[0].score == 2


def test_keyword_matches_empty_query_short_circuits():
    assert search.keyword_matches("   ") == []


def test_keyword_matches_a_query_ending_in_punctuation():
    # \b requires a word/non-word *transition*; it fails for "C++" since the
    # character after the match ("+") and the one after that (e.g. a space)
    # are both non-word, so no transition ever occurs there.
    _insert_note("n1", "I love C++ programming.", title="n1")

    hits = search.keyword_matches("C++")

    assert len(hits) == 1


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
        lambda vector, limit: [{"id": "chunk-a", "score": 0.93, "payload": {"note_id": "note-1"}}],
    )

    hits = search.semantic_search_notes("hair products", 10)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.id == "note-1"
    assert hit.title == "Hair"
    assert hit.path == "/hair"
    assert hit.score == 0.93


def test_semantic_search_skips_orphaned_vector(monkeypatch):
    # Qdrant still has chunk-vectors for a note that's since been deleted
    # from sqlite — must be dropped, not surfaced as a hit with no content.
    monkeypatch.setattr(search, "embed_text", lambda q: [0.1])
    monkeypatch.setattr(
        search,
        "search_vectors",
        lambda vector, limit: [{"id": "chunk-a", "score": 0.5, "payload": {"note_id": "deleted-note"}}],
    )
    assert search.semantic_search_notes("anything", 10) == []


def test_semantic_search_uses_notes_best_matching_chunk(monkeypatch):
    _insert_note("note-1", "irrelevant stuff", path="/a", title="A")
    _insert_note("note-2", "irrelevant stuff too", path="/b", title="B")

    monkeypatch.setattr(search, "embed_text", lambda q: [0.1])
    monkeypatch.setattr(
        search,
        "search_vectors",
        lambda vector, limit: [
            {"id": "n1-chunk0", "score": 0.3, "payload": {"note_id": "note-1"}},
            {"id": "n1-chunk1", "score": 0.9, "payload": {"note_id": "note-1"}},  # note-1's best chunk
            {"id": "n2-chunk0", "score": 0.6, "payload": {"note_id": "note-2"}},
        ],
    )

    hits = search.semantic_search_notes("q", 10)

    assert [h.id for h in hits] == ["note-1", "note-2"]
    assert hits[0].score == 0.9  # the best chunk's score, not an average
    assert hits[1].score == 0.6


def test_semantic_search_ignores_points_from_before_chunking(monkeypatch):
    # A point upserted by the old note-level (pre-chunking) code path has no
    # note_id payload — must be skipped, not crash the whole search, until
    # the collection is reindexed into the new chunked format.
    _insert_note("note-1", "some content", path="/a", title="A")

    monkeypatch.setattr(search, "embed_text", lambda q: [0.1])
    monkeypatch.setattr(
        search,
        "search_vectors",
        lambda vector, limit: [
            {"id": "old-point", "score": 0.9, "payload": {}},
            {"id": "new-chunk", "score": 0.5, "payload": {"note_id": "note-1"}},
        ],
    )

    hits = search.semantic_search_notes("q", 10)

    assert [h.id for h in hits] == ["note-1"]
    assert hits[0].score == 0.5


def _hit(cls, note_id, **overrides):
    fields = {"id": note_id, "title": note_id, "path": "/", "preview": "", "created_at": "t"}
    fields.update(overrides)
    return cls(**fields)


def test_hybrid_search_empty_query_short_circuits(monkeypatch):
    called = []
    monkeypatch.setattr(search, "keyword_matches", lambda q: called.append("kw") or [])
    monkeypatch.setattr(search, "semantic_search_notes", lambda q, limit: called.append("sem") or [])
    assert search.hybrid_search_notes("   ", 10) == ([], 0)
    assert called == []


def test_hybrid_search_ranks_notes_found_by_both_searches_highest(monkeypatch):
    monkeypatch.setattr(
        search,
        "keyword_matches",
        lambda q: [
            _hit(search.SearchHit, "kw-only", score=1),
            _hit(search.SearchHit, "both", score=1),
        ],
    )
    monkeypatch.setattr(
        search,
        "semantic_search_notes",
        lambda q, limit: [
            _hit(search.SemanticSearchHit, "both", score=0.9),
            _hit(search.SemanticSearchHit, "sem-only", score=0.5),
        ],
    )

    hits, total = search.hybrid_search_notes("q", 10)

    ids = [h.id for h in hits]
    assert ids[0] == "both"  # ranked in both lists beats ranking #1 in only one
    assert set(ids) == {"kw-only", "both", "sem-only"}
    assert total == 3


def test_hybrid_search_surfaces_an_exact_keyword_match_the_embedding_missed(monkeypatch):
    # The whole point of hybrid search: a note containing the literal query
    # term must not get buried just because its whole-note embedding doesn't
    # rank it near the top semantically.
    monkeypatch.setattr(
        search,
        "keyword_matches",
        lambda q: [_hit(search.SearchHit, "exact-match", score=1)],
    )
    monkeypatch.setattr(
        search,
        "semantic_search_notes",
        lambda q, limit: [_hit(search.SemanticSearchHit, f"other-{i}", score=0.9 - i / 100) for i in range(20)],
    )

    hits, _ = search.hybrid_search_notes("vehicle", 10)

    assert "exact-match" in [h.id for h in hits]


def test_hybrid_search_respects_limit_but_reports_the_full_total(monkeypatch):
    monkeypatch.setattr(
        search,
        "keyword_matches",
        lambda q: [_hit(search.SearchHit, str(i), score=1) for i in range(5)],
    )
    monkeypatch.setattr(search, "semantic_search_notes", lambda q, limit: [])

    hits, total = search.hybrid_search_notes("q", 2)

    assert len(hits) == 2
    assert total == 5  # the full fused set, not just the returned page


def test_hybrid_search_offset_pages_into_the_fused_ranking(monkeypatch):
    monkeypatch.setattr(
        search,
        "keyword_matches",
        lambda q: [_hit(search.SearchHit, str(i), score=1) for i in range(5)],
    )
    monkeypatch.setattr(search, "semantic_search_notes", lambda q, limit: [])

    hits, total = search.hybrid_search_notes("q", limit=2, offset=2)

    assert [h.id for h in hits] == ["2", "3"]
    assert total == 5


def test_hybrid_search_semantic_pool_matches_total_note_count_not_requested_limit(monkeypatch):
    # A pool scaled by the requested limit/offset would make "total" appear
    # to shift as the user pages deeper (a larger pool reveals more distinct
    # notes) — fetching a ranking over every note instead keeps it stable.
    for i in range(7):
        _insert_note(f"n{i}", f"content {i}")

    monkeypatch.setattr(search, "keyword_matches", lambda q: [])
    captured = {}

    def fake_semantic_search_notes(q, limit):
        captured["pool"] = limit
        return []

    monkeypatch.setattr(search, "semantic_search_notes", fake_semantic_search_notes)

    search.hybrid_search_notes("q", limit=2, offset=50)  # even a deep page...

    assert captured["pool"] == 7  # ...still asks for exactly the total note count


def test_hybrid_search_caps_semantic_pool_at_max_candidate_notes(monkeypatch):
    monkeypatch.setattr(search, "_MAX_HYBRID_CANDIDATE_NOTES", 3)
    for i in range(10):
        _insert_note(f"n{i}", f"content {i}")

    monkeypatch.setattr(search, "keyword_matches", lambda q: [])
    captured = {}

    def fake_semantic_search_notes(q, limit):
        captured["pool"] = limit
        return []

    monkeypatch.setattr(search, "semantic_search_notes", fake_semantic_search_notes)

    search.hybrid_search_notes("q", 10)

    assert captured["pool"] == 3
