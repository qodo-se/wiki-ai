"""The breadcrumb must cost one query per search, not one per hit.

This is the constraint that makes the feature worth having. The obvious
implementation — call tree.ancestors() for each hit — issues up to MAX_DEPTH
queries per result, so a 10-hit page three levels deep would go from a couple
of queries to dozens. Nothing about the response shape reveals which
implementation is in use, so only a cost assertion can hold the line.

Counts statements by proxying the connection, the same technique
test_tree.py uses to assert BEGIN ordering in children().
"""

import pytest

import search
import tree
from db import connect


class _CountingConn:
    """Wraps a real connection, recording every statement executed on it."""

    def __init__(self, inner, log):
        self._inner = inner
        self._log = log

    def execute(self, sql, *args):
        self._log.append(sql.strip().split()[0].upper())
        return self._inner.execute(sql, *args)

    def executemany(self, sql, *args):
        self._log.append(sql.strip().split()[0].upper())
        return self._inner.executemany(sql, *args)

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture
def count_queries(monkeypatch):
    """Returns a callable that runs fn() and gives back the statements it ran.

    Patches connect() in every module that opens its own connection, so the
    count covers the whole call tree rather than just the entry point.
    """

    def _run(fn):
        # Returns (result, statements) so a caller never needs a second call
        # to inspect the result -- the patch is still active after this
        # returns, so a follow-up call would quietly double the count.
        log = []
        for module in (search, tree):
            real = module.connect
            monkeypatch.setattr(
                module, "connect", lambda _real=real: _CountingConn(_real(), log)
            )
        result = fn()
        return result, list(log)

    return _run


def _seed(notes, depth=3):
    """notes matching rows, each `depth` containers deep under one chain."""
    with connect() as db:
        parent = None
        for level in range(depth):
            cid = f"c{level}"
            db.execute(
                "INSERT INTO notes (id, content, kind, parent_id) VALUES (?, ?, ?, ?)",
                (cid, f"# Level {level}", tree.KIND_CONTAINER, parent),
            )
            parent = cid
        for i in range(notes):
            db.execute(
                "INSERT INTO notes (id, content, kind, parent_id) VALUES (?, ?, ?, ?)",
                (f"n{i}", f"note {i} mentions zzyzx", tree.KIND_NOTE, parent),
            )
    return parent


def test_keyword_search_cost_does_not_grow_with_hit_count(count_queries):
    _seed(notes=10, depth=3)

    hits, log = count_queries(lambda: search.keyword_matches("zzyzx"))

    assert len(hits) == 10, "fixture must actually produce 10 hits"
    # Exactly two: the matching rows, and the container map. A per-hit
    # ancestors() walk would be 30+.
    assert log.count("SELECT") == 2, f"too many queries for 10 hits: {log}"


def test_keyword_search_cost_is_the_same_for_one_hit_and_many(count_queries):
    # The real invariant: cost is flat in the number of results.
    _seed(notes=1, depth=3)
    one_hit, few = count_queries(lambda: search.keyword_matches("zzyzx"))

    with connect() as db:
        for i in range(1, 25):
            db.execute(
                "INSERT INTO notes (id, content, kind, parent_id) VALUES (?, ?, ?, ?)",
                (f"m{i}", f"note {i} mentions zzyzx", tree.KIND_NOTE, "c2"),
            )
    many_hits, many = count_queries(lambda: search.keyword_matches("zzyzx"))

    assert (len(one_hit), len(many_hits)) == (1, 25)
    assert many.count("SELECT") == few.count("SELECT"), (
        f"query count grew with hits: 1 hit -> {few}, 25 hits -> {many}"
    )


def test_semantic_search_cost_does_not_grow_with_hit_count(count_queries, monkeypatch):
    _seed(notes=10, depth=3)
    monkeypatch.setattr(search, "embed_text", lambda q: [0.1])
    monkeypatch.setattr(
        search,
        "search_vectors",
        lambda vector, limit: [
            {"id": f"chunk-{i}", "score": 0.9 - i / 100, "payload": {"note_id": f"n{i}"}}
            for i in range(10)
        ],
    )

    hits, log = count_queries(lambda: search.semantic_search_notes("zzyzx", 10))

    assert len(hits) == 10
    assert log.count("SELECT") == 2, f"too many queries for 10 hits: {log}"


def test_hybrid_search_cost_stays_bounded(count_queries, monkeypatch):
    _seed(notes=10, depth=3)
    monkeypatch.setattr(search, "embed_text", lambda q: [0.1])
    monkeypatch.setattr(
        search,
        "search_vectors",
        lambda vector, limit: [
            {"id": f"chunk-{i}", "score": 0.9 - i / 100, "payload": {"note_id": f"n{i}"}}
            for i in range(10)
        ],
    )

    (page, total), log = count_queries(lambda: search.hybrid_search_notes("zzyzx", 10))

    assert total == 10
    # Scope count + keyword rows + keyword containers + semantic rows +
    # semantic containers. Flat in the number of hits, which is the point.
    assert log.count("SELECT") <= 5, f"too many queries: {log}"


def test_breadcrumbs_are_actually_populated_at_depth(count_queries):
    # Guards the obvious way to pass the cost tests: don't build breadcrumbs.
    _seed(notes=3, depth=3)

    hits, _ = count_queries(lambda: search.keyword_matches("zzyzx"))

    assert len(hits) == 3
    assert all(h.breadcrumb == "Level 0 › Level 1 › Level 2" for h in hits), [
        h.breadcrumb for h in hits
    ]
