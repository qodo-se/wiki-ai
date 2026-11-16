"""HTTP-layer behaviour of the knowledge tree.

tree.py's own tests cover the logic; these cover the wiring — status codes,
response shapes, and the route-shadowing hazard of hanging literal sub-paths
off a router that already declares GET /{uuid}.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import app as app_module

    return TestClient(app_module.app)


def _note(client, content="a note", **kw):
    res = client.post("/api/v1/notes", json={"content": content, **kw})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _container(client, name, body=""):
    return _note(client, content=f"# {name}\n\n{body}", kind="container")


# --- creating containers (Req 2) -------------------------------------------


def test_create_container_round_trips_its_markdown(client):
    content = "# UH-100\n\nA 100-level college survey course, 2018."
    res = client.post("/api/v1/notes", json={"content": content, "kind": "container"})
    assert res.status_code == 200

    note_id = res.json()["id"]
    assert client.get(f"/api/v1/notes/{note_id}").text == content
    assert client.get(f"/api/v1/notes/{note_id}/meta").json()["kind"] == "container"


def test_create_defaults_to_a_plain_note(client):
    note_id = _note(client)
    assert client.get(f"/api/v1/notes/{note_id}/meta").json()["kind"] == "note"


def test_create_rejects_an_unknown_kind(client):
    res = client.post("/api/v1/notes", json={"content": "x", "kind": "folder"})
    assert res.status_code == 422


def test_create_rejects_a_container_with_no_title_line(client):
    res = client.post("/api/v1/notes", json={"content": "   \n\n  ", "kind": "container"})
    assert res.status_code == 422
    assert "title line" in res.text


def test_a_plain_note_may_still_be_empty(client):
    # Only containers need a derivable title; notes keep today's behaviour.
    assert client.post("/api/v1/notes", json={"content": ""}).status_code == 200


def test_update_cannot_empty_out_a_container(client):
    container_id = _container(client, "Course")
    res = client.put(f"/api/v1/notes/{container_id}", json={"content": "  "})
    assert res.status_code == 422
    assert client.get(f"/api/v1/notes/{container_id}").text.startswith("# Course")


# --- meta exposes tree position (Req 3) ------------------------------------


def test_meta_reports_kind_and_parent(client):
    container_id = _container(client, "Box")
    note_id = _note(client)

    assert client.get(f"/api/v1/notes/{note_id}/meta").json()["parent_id"] is None

    client.put(f"/api/v1/notes/{note_id}/parent", json={"parent_id": container_id})
    meta = client.get(f"/api/v1/notes/{note_id}/meta").json()
    assert meta["parent_id"] == container_id
    assert meta["kind"] == "note"


# --- setting a parent (Req 4) ----------------------------------------------


def test_put_parent_sets_and_clears(client):
    container_id = _container(client, "Box")
    note_id = _note(client)

    assert client.put(f"/api/v1/notes/{note_id}/parent", json={"parent_id": container_id}).status_code == 200
    assert client.get(f"/api/v1/notes/{note_id}/meta").json()["parent_id"] == container_id

    assert client.put(f"/api/v1/notes/{note_id}/parent", json={"parent_id": None}).status_code == 200
    assert client.get(f"/api/v1/notes/{note_id}/meta").json()["parent_id"] is None


@pytest.mark.parametrize(
    "case,expect_fragment",
    [
        ("missing", "not found"),
        ("not_a_container", "not a container"),
        ("self", "own parent"),
        ("cycle", "cycle"),
    ],
)
def test_put_parent_rejects_bad_assignments_with_400(client, case, expect_fragment):
    outer = _container(client, "Outer")
    inner = _container(client, "Inner")
    client.put(f"/api/v1/notes/{inner}/parent", json={"parent_id": outer})
    plain = _note(client)

    target, parent = {
        "missing": (plain, "no-such-id"),
        "not_a_container": (plain, _note(client, "another plain note")),
        "self": (plain, plain),
        "cycle": (outer, inner),
    }[case]

    res = client.put(f"/api/v1/notes/{target}/parent", json={"parent_id": parent})

    assert res.status_code == 400
    assert expect_fragment in res.json()["detail"]
    # the rejected assignment left the stored parent untouched
    assert client.get(f"/api/v1/notes/{target}/meta").json()["parent_id"] != parent


def test_put_parent_404s_on_an_unknown_note(client):
    res = client.put("/api/v1/notes/nope/parent", json={"parent_id": None})
    assert res.status_code == 404


# --- ancestors (Req 5) ------------------------------------------------------


def test_ancestors_returns_the_chain_root_first_with_prose(client):
    education = _container(client, "education")
    uh100 = _container(client, "UH-100", "A 100-level college survey course, 2018.")
    foundations = _container(client, "US-History-Foundations")
    essay = _note(client, "# Reconstruction essay")

    for child, parent in ((uh100, education), (foundations, uh100), (essay, foundations)):
        assert client.put(f"/api/v1/notes/{child}/parent", json={"parent_id": parent}).status_code == 200

    items = client.get(f"/api/v1/notes/{essay}/ancestors").json()["items"]

    assert [a["title"] for a in items] == ["education", "UH-100", "US-History-Foundations"]
    assert "100-level college survey course" in items[1]["content"]
    assert items[1]["content_truncated"] is False


def test_ancestors_is_empty_for_a_top_level_note(client):
    assert client.get(f"/api/v1/notes/{_note(client)}/ancestors").json()["items"] == []


def test_ancestors_404s_on_an_unknown_note(client):
    assert client.get("/api/v1/notes/nope/ancestors").status_code == 404


# --- children (Req 6) -------------------------------------------------------


def test_children_lists_notes_and_sub_containers(client):
    box = _container(client, "Box")
    sub = _container(client, "Sub")
    n1, n2 = _note(client, "one"), _note(client, "two")
    for child in (sub, n1, n2):
        client.put(f"/api/v1/notes/{child}/parent", json={"parent_id": box})

    body = client.get(f"/api/v1/notes/{box}/children").json()

    assert body["total"] == 3
    assert {i["id"] for i in body["items"]} == {sub, n1, n2}
    assert body["items"][0]["kind"] == "container"  # containers sort first


def test_children_pages_without_changing_the_total(client):
    box = _container(client, "Box")
    for i in range(3):
        client.put(f"/api/v1/notes/{_note(client, f'n{i}')}/parent", json={"parent_id": box})

    body = client.get(f"/api/v1/notes/{box}/children?limit=2&offset=2").json()

    assert body["total"] == 3
    assert len(body["items"]) == 1


def test_children_of_a_plain_note_is_an_empty_page(client):
    body = client.get(f"/api/v1/notes/{_note(client)}/children").json()
    assert (body["items"], body["total"]) == ([], 0)


def test_children_404s_on_an_unknown_note(client):
    assert client.get("/api/v1/notes/nope/children").status_code == 404


# --- the skeleton in one call (Req 7) ---------------------------------------


def test_listing_containers_returns_the_whole_skeleton_in_one_request(client):
    education = _container(client, "education")
    uh100 = _container(client, "UH-100")
    foundations = _container(client, "US-History-Foundations")
    presidents = _container(client, "US-Presidents")
    for child, parent in (
        (uh100, education), (foundations, uh100), (presidents, uh100),
    ):
        client.put(f"/api/v1/notes/{child}/parent", json={"parent_id": parent})
    _note(client, "a note that should not appear")

    body = client.get("/api/v1/notes?scope=containers&limit=100").json()

    assert body["total"] == 4
    hierarchy = {i["id"]: i["parent_id"] for i in body["items"]}
    assert hierarchy == {
        education: None,
        uh100: education,
        foundations: uh100,
        presidents: uh100,
    }


# --- delete promotes children (Req 10) --------------------------------------


def test_deleting_a_container_promotes_children_to_its_own_parent(client):
    a = _container(client, "A")
    b = _container(client, "B")
    client.put(f"/api/v1/notes/{b}/parent", json={"parent_id": a})
    n1, n2 = _note(client, "one"), _note(client, "two")
    for n in (n1, n2):
        client.put(f"/api/v1/notes/{n}/parent", json={"parent_id": b})

    assert client.delete(f"/api/v1/notes/{b}").status_code == 204

    assert client.get(f"/api/v1/notes/{b}").status_code == 404
    for n in (n1, n2):
        assert client.get(f"/api/v1/notes/{n}/meta").json()["parent_id"] == a


def test_deleting_a_top_level_container_sends_children_to_top_level(client):
    b = _container(client, "B")
    n1 = _note(client, "one")
    client.put(f"/api/v1/notes/{n1}/parent", json={"parent_id": b})

    client.delete(f"/api/v1/notes/{b}")

    assert client.get(f"/api/v1/notes/{n1}/meta").json()["parent_id"] is None


def test_deleting_a_container_deletes_nothing_else(client):
    b = _container(client, "B")
    n1 = _note(client, "one")
    client.put(f"/api/v1/notes/{n1}/parent", json={"parent_id": b})

    client.delete(f"/api/v1/notes/{b}")

    assert client.get("/api/v1/notes?scope=all").json()["total"] == 1


def test_delete_still_404s_on_an_unknown_note(client):
    assert client.delete("/api/v1/notes/nope").status_code == 404


# --- route shadowing --------------------------------------------------------


def test_literal_subpaths_are_not_swallowed_by_the_uuid_route(client):
    # routes/notes.py declares GET /{uuid} before these; a note whose id were
    # matched greedily would turn /ancestors into note content.
    note_id = _note(client, "# Plain")
    for suffix in ("meta", "ancestors", "children"):
        res = client.get(f"/api/v1/notes/{note_id}/{suffix}")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("application/json")
