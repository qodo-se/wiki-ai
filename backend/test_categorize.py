import pytest

import categorize
import db


def _insert_note(note_id, content, path="/", title=""):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO notes (id, content, path, title) VALUES (?, ?, ?, ?)",
            (note_id, content, path, title),
        )


# --- _UnionFind -------------------------------------------------------------


def test_union_find_merges_transitively():
    uf = categorize._UnionFind(["a", "b", "c", "d"])
    uf.union("a", "b")
    uf.union("b", "c")
    components = {frozenset(c) for c in uf.components()}
    assert frozenset({"a", "b", "c"}) in components
    assert frozenset({"d"}) in components


def test_union_find_no_unions_gives_all_singletons():
    uf = categorize._UnionFind(["a", "b", "c"])
    components = {frozenset(c) for c in uf.components()}
    assert components == {frozenset({"a"}), frozenset({"b"}), frozenset({"c"})}


# --- _build_neighbor_graph / _cluster_ids -----------------------------------


def test_build_neighbor_graph_requires_mutual_top_k(monkeypatch):
    # a's #1 neighbor is b, and b's #1 neighbor is a -> mutual, connected.
    # c's #1 neighbor is a, but a's #1 neighbor is b, not c -> not mutual, no edge.
    vectors = {"a": [1.0], "b": [2.0], "c": [3.0]}
    responses = {
        "a": [
            {"id": "a", "score": 1.0},  # self-match, must be excluded
            {"id": "b", "score": 0.9},
            {"id": "c", "score": 0.5},
        ],
        "b": [
            {"id": "b", "score": 1.0},
            {"id": "a", "score": 0.95},
            {"id": "c", "score": 0.3},
        ],
        "c": [
            {"id": "c", "score": 1.0},
            {"id": "a", "score": 0.6},
            {"id": "b", "score": 0.4},
        ],
    }

    def fake_search(vector, limit):
        note_id = next(k for k, v in vectors.items() if v == vector)
        return responses[note_id]

    monkeypatch.setattr(categorize, "search_vectors", fake_search)
    graph = categorize._build_neighbor_graph(vectors, neighbor_limit=1)
    assert graph["a"] == {"b"}
    assert graph["b"] == {"a"}
    assert graph["c"] == set()  # not mutual with anyone at K=1


def test_build_neighbor_graph_ignores_unknown_ids(monkeypatch):
    vectors = {"a": [1.0], "b": [2.0]}

    def fake_search(vector, limit):
        return [
            {"id": "a", "score": 1.0},
            {"id": "unknown", "score": 0.99},  # not in this note set, excluded
            {"id": "b", "score": 0.5},
        ]

    monkeypatch.setattr(categorize, "search_vectors", fake_search)
    graph = categorize._build_neighbor_graph(vectors, neighbor_limit=10)
    assert graph["a"] == {"b"}
    assert graph["b"] == {"a"}


def test_cluster_ids_groups_connected_notes():
    graph = {"a": {"b"}, "b": {"a"}, "c": set()}
    clusters = categorize._cluster_ids(["a", "b", "c"], graph)
    sets = {frozenset(c) for c in clusters}
    assert frozenset({"a", "b"}) in sets
    assert frozenset({"c"}) in sets


# --- _sanitize_path ----------------------------------------------------------


def test_sanitize_path_adds_leading_slash():
    assert categorize._sanitize_path("recipes") == "/recipes"


def test_sanitize_path_strips_quotes_and_backticks():
    assert categorize._sanitize_path('`"/recipes"`') == "/recipes"


def test_sanitize_path_takes_first_line_only():
    assert categorize._sanitize_path("/recipes\nextra commentary here") == "/recipes"


def test_sanitize_path_strips_disallowed_characters():
    assert categorize._sanitize_path("/recipes!!ok??") == "/recipesok"


def test_sanitize_path_rejects_blank_or_root_only():
    assert categorize._sanitize_path("") is None
    assert categorize._sanitize_path("   ") is None
    assert categorize._sanitize_path("/") is None
    assert categorize._sanitize_path("///") is None


# --- _extract_json -----------------------------------------------------------


def test_extract_json_plain():
    assert categorize._extract_json('{"a": "b"}') == {"a": "b"}


def test_extract_json_markdown_fenced():
    text = '```json\n{"a": "b"}\n```'
    assert categorize._extract_json(text) == {"a": "b"}


def test_extract_json_invalid_returns_none():
    assert categorize._extract_json("not json at all") is None


# --- _consolidate_names -------------------------------------------------------


def test_consolidate_names_skips_llm_call_for_single_name(monkeypatch):
    called = False

    def fake_generate(prompt):
        nonlocal called
        called = True
        return "{}"

    monkeypatch.setattr(categorize, "generate_text", fake_generate)
    result = categorize._consolidate_names(["/books"])
    assert result == {"/books": "/books"}
    assert not called


def test_consolidate_names_merges_duplicates(monkeypatch):
    monkeypatch.setattr(
        categorize,
        "generate_text",
        lambda prompt: '{"/recipes": "/recipes", "/cooking": "/recipes"}',
    )
    result = categorize._consolidate_names(["/recipes", "/cooking"])
    assert result == {"/recipes": "/recipes", "/cooking": "/recipes"}


def test_consolidate_names_falls_back_to_identity_on_bad_json(monkeypatch):
    monkeypatch.setattr(categorize, "generate_text", lambda prompt: "not json")
    result = categorize._consolidate_names(["/books", "/recipes"])
    assert result == {"/books": "/books", "/recipes": "/recipes"}


def test_consolidate_names_falls_back_on_llm_error(monkeypatch):
    def raise_error(prompt):
        raise categorize.LLMError("down")

    monkeypatch.setattr(categorize, "generate_text", raise_error)
    result = categorize._consolidate_names(["/books", "/recipes"])
    assert result == {"/books": "/books", "/recipes": "/recipes"}


# --- reorganize_notes (integration, mocked Ollama/Qdrant) --------------------


def test_reorganize_notes_too_few_notes_is_a_noop():
    _insert_note("only-one", "just one note")
    result = categorize.reorganize_notes()
    assert result == {"clusters": 0, "moved": 0, "unchanged": 1, "singletons": 1}


def test_reorganize_notes_groups_and_moves(monkeypatch):
    _insert_note("book-a", "Dune: a desert planet story", title="Dune")
    _insert_note("book-b", "1984: a dystopian future", title="1984")
    _insert_note("recipe-a", "Pad Thai: rice noodles and shrimp", title="Pad Thai")
    _insert_note("recipe-b", "Beef Tacos: seasoned beef in tortillas", title="Beef Tacos")
    _insert_note("lonely", "A completely unrelated one-off note", title="Lonely")

    vectors = {
        "book-a": [1.0, 0.0, 0.0],
        "book-b": [0.9, 0.1, 0.0],
        "recipe-a": [0.0, 1.0, 0.0],
        "recipe-b": [0.0, 0.9, 0.1],
        "lonely": [0.0, 0.0, 1.0],
    }
    neighbors = {
        "book-a": [{"id": "book-a", "score": 1.0}, {"id": "book-b", "score": 0.95}],
        "book-b": [{"id": "book-b", "score": 1.0}, {"id": "book-a", "score": 0.95}],
        "recipe-a": [{"id": "recipe-a", "score": 1.0}, {"id": "recipe-b", "score": 0.93}],
        "recipe-b": [{"id": "recipe-b", "score": 1.0}, {"id": "recipe-a", "score": 0.93}],
        "lonely": [{"id": "lonely", "score": 1.0}],
    }

    monkeypatch.setattr(
        categorize,
        "scroll_all_points",
        lambda: [{"id": k, "vector": v} for k, v in vectors.items()],
    )
    monkeypatch.setattr(categorize, "search_vectors", lambda vector, limit: (
        neighbors[next(k for k, v in vectors.items() if v == vector)]
    ))

    def fake_generate(prompt):
        if "consolidated path" in prompt:
            return '{"/books": "/books", "/recipes": "/recipes"}'
        if "Dune" in prompt:
            return "/books"
        if "Pad Thai" in prompt:
            return "/recipes"
        raise AssertionError(f"unexpected prompt: {prompt}")

    monkeypatch.setattr(categorize, "generate_text", fake_generate)

    result = categorize.reorganize_notes()

    assert result["clusters"] == 2
    assert result["singletons"] == 1
    assert result["moved"] == 4  # all 4 clustered notes started at "/"

    notes = categorize._fetch_all_notes()
    assert notes["book-a"]["path"] == "/books"
    assert notes["book-b"]["path"] == "/books"
    assert notes["recipe-a"]["path"] == "/recipes"
    assert notes["recipe-b"]["path"] == "/recipes"
    assert notes["lonely"]["path"] == "/"  # untouched singleton


def test_reorganize_notes_rejects_concurrent_calls():
    _insert_note("only-one", "just one note")
    categorize._reorganize_lock.acquire()
    try:
        with pytest.raises(categorize.ReorganizeInProgress):
            categorize.reorganize_notes()
    finally:
        categorize._reorganize_lock.release()


def test_reorganize_notes_survives_vectorstore_outage(monkeypatch):
    _insert_note("a", "note a")
    _insert_note("b", "note b")

    def raise_error():
        raise categorize.VectorStoreError("qdrant is down")

    monkeypatch.setattr(categorize, "scroll_all_points", raise_error)

    result = categorize.reorganize_notes()

    assert result["clusters"] == 0
    assert result["moved"] == 0
    assert result["unchanged"] == 2
