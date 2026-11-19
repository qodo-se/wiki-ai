"""Containers must never leak into ordinary note results.

This is the one defect this feature genuinely can't afford, and it's the kind
a convention doesn't catch: the scope predicate has to be applied in four
separate query paths *and* in each one's `total`, so a single forgotten call
site is a silent leak. These tests assert the behaviour at every public read
surface rather than trusting that every query remembered to filter.

Every test uses the distinctive token "zzyzx" so a hit can only have come from
the container, never from incidental content elsewhere.
"""

import pytest
from fastapi.testclient import TestClient

import search as search_module

TOKEN = "zzyzx"


@pytest.fixture
def client():
    # Imported lazily so the temp_db fixture has already pointed db.DB_PATH at
    # a per-test tmp file before app.py's module-level `db.connect().close()`
    # runs — same constraint as test_images.py.
    import app as app_module

    return TestClient(app_module.app)


@pytest.fixture
def container_and_note(client):
    """A container whose body contains TOKEN, and a plain note that doesn't."""
    container = client.post(
        "/api/v1/notes",
        json={"content": f"# Course\n\nThis container mentions {TOKEN}.", "kind": "container"},
    )
    assert container.status_code == 200
    note = client.post("/api/v1/notes", json={"content": "an ordinary note"})
    assert note.status_code == 200
    return container.json()["id"], note.json()["id"]


# --- GET /api/v1/notes -----------------------------------------------------


def test_list_notes_hides_containers_by_default(client, container_and_note):
    container_id, note_id = container_and_note

    body = client.get("/api/v1/notes").json()

    assert [i["id"] for i in body["items"]] == [note_id]
    assert body["total"] == 1, "total must exclude containers too, not just items"


def test_list_notes_can_ask_for_containers(client, container_and_note):
    container_id, _ = container_and_note

    body = client.get("/api/v1/notes?scope=containers").json()

    assert [i["id"] for i in body["items"]] == [container_id]
    assert body["total"] == 1


def test_list_notes_scope_all_returns_both(client, container_and_note):
    container_id, note_id = container_and_note

    body = client.get("/api/v1/notes?scope=all").json()

    assert {i["id"] for i in body["items"]} == {container_id, note_id}
    assert body["total"] == 2


def test_list_notes_rejects_an_unknown_scope(client):
    assert client.get("/api/v1/notes?scope=sideways").status_code == 422


# --- the "By Path" full-set fetch ------------------------------------------


def test_by_path_full_set_fetch_never_sees_a_container(client, container_and_note):
    # static/js/app.js builds the By Path view by paging /api/v1/notes with
    # limit=100 until it has everything. Walk the same path it walks.
    container_id, note_id = container_and_note
    collected, offset = [], 0
    while True:
        body = client.get(f"/api/v1/notes?limit=100&offset={offset}").json()
        collected.extend(body["items"])
        offset += len(body["items"])
        if not body["items"] or offset >= body["total"]:
            break

    assert [i["id"] for i in collected] == [note_id]
    assert container_id not in {i["id"] for i in collected}


# --- GET /api/v1/search ----------------------------------------------------


def test_keyword_search_hides_containers_by_default(client, container_and_note):
    body = client.get(f"/api/v1/search?q={TOKEN}").json()

    assert body["items"] == []
    assert body["total"] == 0, "total must exclude containers too, not just items"


def test_keyword_search_can_ask_for_containers(client, container_and_note):
    container_id, _ = container_and_note

    body = client.get(f"/api/v1/search?q={TOKEN}&scope=containers").json()

    assert [i["id"] for i in body["items"]] == [container_id]
    assert body["total"] == 1


def test_keyword_search_scope_all_finds_the_container(client, container_and_note):
    container_id, _ = container_and_note

    body = client.get(f"/api/v1/search?q={TOKEN}&scope=all").json()

    assert [i["id"] for i in body["items"]] == [container_id]


def test_keyword_search_rejects_an_unknown_scope(client):
    assert client.get("/api/v1/search?q=x&scope=sideways").status_code == 422


# --- GET /api/v1/search/hybrid ---------------------------------------------


@pytest.fixture
def fake_vectors(monkeypatch, container_and_note):
    """Makes Qdrant 'return' the container as its top semantic hit.

    The semantic half can't filter in the vector store — Qdrant doesn't know
    what kind a note is — so the filter has to hold when chunk hits are
    hydrated back into rows. Forcing the container to the top of the vector
    results is the only way to prove it does.
    """
    container_id, _ = container_and_note
    monkeypatch.setattr(search_module, "embed_text", lambda q: [0.1])
    monkeypatch.setattr(
        search_module,
        "search_vectors",
        lambda vector, limit: [
            {"id": "chunk-a", "score": 0.99, "payload": {"note_id": container_id}}
        ],
    )
    return container_id


def test_hybrid_search_hides_containers_by_default(client, fake_vectors):
    body = client.get(f"/api/v1/search/hybrid?q={TOKEN}").json()

    assert body["items"] == []
    assert body["total"] == 0, "total must exclude containers too, not just items"


def test_hybrid_search_can_ask_for_containers(client, fake_vectors):
    body = client.get(f"/api/v1/search/hybrid?q={TOKEN}&scope=containers").json()

    assert [i["id"] for i in body["items"]] == [fake_vectors]
    assert body["total"] == 1


def test_hybrid_search_scope_all_finds_the_container(client, fake_vectors):
    body = client.get(f"/api/v1/search/hybrid?q={TOKEN}&scope=all").json()

    assert fake_vectors in [i["id"] for i in body["items"]]


def test_hybrid_search_rejects_an_unknown_scope(client):
    assert client.get("/api/v1/search/hybrid?q=x&scope=sideways").status_code == 422


# --- the semantic half specifically ----------------------------------------


def test_semantic_half_filters_containers_when_hydrating(monkeypatch, client, fake_vectors):
    # Calls the function directly rather than through the route, so a future
    # change that filters only in the route (and not in the shared function
    # hybrid search also uses) still fails here.
    from tree import Scope

    assert search_module.semantic_search_notes(TOKEN, 10) == []
    assert search_module.semantic_search_notes(TOKEN, 10, Scope.containers)[0].id == fake_vectors


def test_semantic_half_does_not_let_a_container_consume_a_result_slot(monkeypatch, client):
    # Regression: the scope filter runs in SQL, so trimming the ranked ids to
    # `limit` before it ran let a higher-ranked container eat the only slot and
    # then get filtered out — returning nothing while a matching note sat right
    # behind it.
    container = client.post(
        "/api/v1/notes",
        json={"content": f"# Course\n\n{TOKEN}", "kind": "container"},
    ).json()["id"]
    note = client.post("/api/v1/notes", json={"content": f"a note mentioning {TOKEN}"}).json()["id"]

    monkeypatch.setattr(search_module, "embed_text", lambda q: [0.1])
    monkeypatch.setattr(
        search_module,
        "search_vectors",
        lambda vector, limit: [
            {"id": "c0", "score": 0.99, "payload": {"note_id": container}},  # outranks the note
            {"id": "c1", "score": 0.50, "payload": {"note_id": note}},
        ],
    )

    hits = search_module.semantic_search_notes(TOKEN, 1)

    assert [h.id for h in hits] == [note]


def test_semantic_half_still_honours_limit(monkeypatch, client):
    ids = [client.post("/api/v1/notes", json={"content": f"note {i} {TOKEN}"}).json()["id"] for i in range(3)]
    monkeypatch.setattr(search_module, "embed_text", lambda q: [0.1])
    monkeypatch.setattr(
        search_module,
        "search_vectors",
        lambda vector, limit: [
            {"id": f"c{i}", "score": 0.9 - i / 100, "payload": {"note_id": nid}}
            for i, nid in enumerate(ids)
        ],
    )

    assert len(search_module.semantic_search_notes(TOKEN, 2)) == 2
