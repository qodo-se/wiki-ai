import db


def test_fresh_empty_install_marks_current_version_without_reindexing(monkeypatch):
    # The very first connect() on a brand-new database has zero notes — marking
    # the version directly (no Ollama/Qdrant call needed) is what keeps the rest
    # of the test suite fast, since every test's first connect() hits this path.
    import reindex

    monkeypatch.setattr(
        reindex, "reindex_notes", lambda: (_ for _ in ()).throw(AssertionError("should not be called"))
    )

    conn = db.connect()

    assert db._embedding_index_version(conn) == db.EMBEDDING_INDEX_VERSION


def test_reindex_is_triggered_when_stored_version_is_behind(monkeypatch):
    conn = db.connect()  # first connect() of this (simulated) process: 0 notes, fast-path marks current
    conn.execute("INSERT INTO notes (id, content) VALUES ('n1', 'hello')")
    conn.execute("DELETE FROM embedding_index_state")  # simulate an upgrade from before this existed
    conn.commit()

    import reindex

    calls = []
    monkeypatch.setattr(
        reindex, "reindex_notes", lambda: calls.append(True) or {"total": 1, "embedded": 1, "failed": 0}
    )
    monkeypatch.setattr(db, "_embedding_reindex_checked", False)  # simulate the next process restart

    conn = db.connect()

    assert calls == [True]
    assert db._embedding_index_version(conn) == db.EMBEDDING_INDEX_VERSION


def test_reindex_is_skipped_once_version_is_current(monkeypatch):
    conn = db.connect()  # 0 notes — fast path already marks the version current
    conn.execute("INSERT INTO notes (id, content) VALUES ('n1', 'hello')")
    conn.commit()

    import reindex

    monkeypatch.setattr(
        reindex, "reindex_notes", lambda: (_ for _ in ()).throw(AssertionError("should not be called"))
    )
    monkeypatch.setattr(db, "_embedding_reindex_checked", False)  # simulate the next process restart

    db.connect()  # must not raise / must not call reindex_notes


def test_reindex_check_runs_at_most_once_per_process_even_if_version_stays_stale(monkeypatch):
    # connect() is called on every request, not just at startup — if Ollama/Qdrant
    # stays down, later connect() calls within the same process must not each
    # attempt another corpus-wide reindex; only an actual restart retries.
    conn = db.connect()
    conn.execute("INSERT INTO notes (id, content) VALUES ('n1', 'hello')")
    conn.execute("DELETE FROM embedding_index_state")
    conn.commit()

    import reindex

    calls = []

    def fake_reindex_notes():
        calls.append(True)
        raise reindex.EmbeddingError("ollama unreachable")

    monkeypatch.setattr(reindex, "reindex_notes", fake_reindex_notes)
    monkeypatch.setattr(db, "_embedding_reindex_checked", False)  # simulate the one real startup check

    db.connect()  # first call after "startup": attempts and fails
    db.connect()  # a later request-driven connect(): must not attempt again

    assert calls == [True]


def test_reindex_failure_does_not_crash_connect_and_leaves_version_unmarked(monkeypatch):
    conn = db.connect()
    conn.execute("INSERT INTO notes (id, content) VALUES ('n1', 'hello')")
    conn.execute("DELETE FROM embedding_index_state")
    conn.commit()

    import reindex

    def fake_reindex_notes():
        raise reindex.EmbeddingError("ollama unreachable")

    monkeypatch.setattr(reindex, "reindex_notes", fake_reindex_notes)
    monkeypatch.setattr(db, "_embedding_reindex_checked", False)

    conn = db.connect()  # must not raise

    assert db._embedding_index_version(conn) == 0  # left unmarked, retries on next restart


def test_reindex_partial_failure_leaves_version_unmarked(monkeypatch):
    # A format-version reindex is supposed to bring every note up to date — settling
    # for "most of them" without retrying would leave some notes permanently missing
    # from search until the next unrelated format bump happens to come along.
    conn = db.connect()
    conn.execute("INSERT INTO notes (id, content) VALUES ('n1', 'hello')")
    conn.execute("DELETE FROM embedding_index_state")
    conn.commit()

    import reindex

    monkeypatch.setattr(
        reindex, "reindex_notes", lambda: {"total": 2, "embedded": 1, "failed": 1}
    )
    monkeypatch.setattr(db, "_embedding_reindex_checked", False)

    conn = db.connect()  # must not raise

    assert db._embedding_index_version(conn) == 0


def test_reindexs_own_internal_connect_call_does_not_recurse_or_deadlock(monkeypatch):
    # reindex_notes() itself calls connect() (to read notes via `with connect() as
    # db:`) — exercising the real reindex_notes() here (with its real lock and real
    # nested connect() call), mocking only the external-service boundary, is what
    # actually proves the reentrancy guard works rather than just asserting it does.
    conn = db.connect()
    conn.execute("INSERT INTO notes (id, content) VALUES ('n1', 'hello')")
    conn.execute("DELETE FROM embedding_index_state")
    conn.commit()

    import reindex

    monkeypatch.setattr(reindex, "embed_text", lambda content: [0.1])
    monkeypatch.setattr(reindex, "delete_collection", lambda: None)
    monkeypatch.setattr(reindex, "upsert_vectors", lambda points: None)
    monkeypatch.setattr(db, "_embedding_reindex_checked", False)

    conn = db.connect()  # must neither hang nor skip due to the nested connect() re-entering

    assert db._embedding_index_version(conn) == db.EMBEDDING_INDEX_VERSION
