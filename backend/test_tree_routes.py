"""HTTP-layer behaviour of the knowledge tree.

tree.py's own tests cover the logic; these cover the wiring — status codes,
response shapes, and the route-shadowing hazard of hanging literal sub-paths
off a router that already declares GET /{uuid}.
"""

import json

import pytest
from fastapi.testclient import TestClient

from db import connect


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


# --- review regressions ----------------------------------------------------


@pytest.mark.parametrize("content", ["#", "#   ", "##\n\n", "   \n#\n"])
def test_a_container_whose_title_derives_to_nothing_is_rejected(client, content):
    # "#" clears the "has a non-blank line" bar but derives to an empty title
    # once the heading marker is stripped — an unnameable node in the tree.
    res = client.post("/api/v1/notes", json={"content": content, "kind": "container"})
    assert res.status_code == 422
    assert "title line" in res.text


def test_update_cannot_reduce_a_container_to_a_bare_heading_marker(client):
    container_id = _container(client, "Course")
    assert client.put(f"/api/v1/notes/{container_id}", json={"content": "#"}).status_code == 422


def test_list_derives_titles_so_the_skeleton_is_readable(client):
    # notes.title is never written, so without derivation every container in a
    # scope=containers listing comes back nameless — leaving the "whole
    # skeleton in one call" promise technically true but useless.
    container_id = _container(client, "UH-100")
    note_id = _note(client, "# Reconstruction essay\n\nbody")

    containers = client.get("/api/v1/notes?scope=containers").json()
    assert [i["title"] for i in containers["items"]] == ["UH-100"]

    notes = client.get("/api/v1/notes").json()
    assert [i["title"] for i in notes["items"]] == ["Reconstruction essay"]


def test_list_titles_agree_with_every_other_read_surface(client):
    container_id = _container(client, "UH-100")
    note_id = _note(client, "# Essay\n\nbody")
    client.put(f"/api/v1/notes/{note_id}/parent", json={"parent_id": container_id})

    from_list = next(
        i["title"] for i in client.get("/api/v1/notes?scope=all").json()["items"]
        if i["id"] == container_id
    )
    from_ancestors = client.get(f"/api/v1/notes/{note_id}/ancestors").json()["items"][0]["title"]
    from_children = client.get(f"/api/v1/notes/{container_id}/children").json()["items"][0]["title"]
    from_search = client.get("/api/v1/search?q=UH-100&scope=containers").json()["items"][0]["title"]

    assert from_list == from_ancestors == from_search == "UH-100"
    assert from_children == "Essay"


def test_list_titles_survive_long_leading_whitespace(client):
    # The title slice is taken after trimming: content with more than 120
    # characters of leading blank lines used to slice to nothing and list as
    # untitled, while ancestors and children still showed the real name.
    padding = "\n" * 200
    container_id = _note(client, content=f"{padding}# Buried Title\n\nbody", kind="container")
    note_id = _note(client, content=f"{padding}# Buried Note\n\nbody")
    client.put(f"/api/v1/notes/{note_id}/parent", json={"parent_id": container_id})

    listed = {i["id"]: i["title"] for i in client.get("/api/v1/notes?scope=all&limit=100").json()["items"]}
    assert listed[container_id] == "Buried Title"
    assert listed[note_id] == "Buried Note"

    # and it agrees with the surfaces that read the whole content
    assert client.get(f"/api/v1/notes/{note_id}/ancestors").json()["items"][0]["title"] == "Buried Title"
    assert client.get(f"/api/v1/notes/{container_id}/children").json()["items"][0]["title"] == "Buried Note"


# --- container previews in listings (Reqs 6, 7, 8) -------------------------


def test_container_preview_is_empty_in_listings(client):
    container_id = _note(
        client, content="# UH-100\n\nA long description that would otherwise be echoed back "
                        "in full on every structural listing of the tree.", kind="container")

    for scope in ("containers", "all"):
        item = next(
            i for i in client.get(f"/api/v1/notes?scope={scope}&limit=100").json()["items"]
            if i["id"] == container_id
        )
        assert "preview" in item, "the key must stay present, not be dropped"
        assert item["preview"] == "", f"scope={scope} leaked a container preview"
        assert item["title"] == "UH-100", "the title still identifies it"


def test_note_previews_are_untouched(client):
    note_id = _note(client, content="# Essay\n\nThe body of an ordinary note.")

    item = next(
        i for i in client.get("/api/v1/notes?scope=all&limit=100").json()["items"]
        if i["id"] == note_id
    )
    assert "The body of an ordinary note" in item["preview"]


def test_container_search_preview_is_still_populated(client):
    # The empty-preview rule is for listings only. In search, the preview is
    # the snippet showing why the row matched, which is the useful part.
    container_id = _note(
        client, content="# Course\n\nA seminar about zzyzxtopic and related things.",
        kind="container")

    hit = client.get("/api/v1/search?q=zzyzxtopic&scope=containers").json()["items"][0]
    assert hit["id"] == container_id
    assert "zzyzxtopic" in hit["preview"], "search must still show why it matched"

    listed = next(
        i for i in client.get("/api/v1/notes?scope=containers&limit=100").json()["items"]
        if i["id"] == container_id
    )
    assert listed["preview"] == ""


def test_skeleton_payload_is_materially_smaller(client):
    # Req 7: with realistic container bodies, dropping the preview must cut
    # the skeleton response by at least 40%.
    body = "x" * 400
    for i in range(50):
        _note(client, content=f"# Container {i}\n\n{body}", kind="container")

    actual = client.get("/api/v1/notes?scope=containers&limit=100").json()
    assert actual["total"] == 50

    actual_bytes = len(json.dumps(actual))
    # What the same response would have weighed with previews restored.
    with_previews = json.loads(json.dumps(actual))
    for item in with_previews["items"]:
        item["preview"] = f"# {item['title']}\n\n{body}"[:120]
    before_bytes = len(json.dumps(with_previews))

    saved = 1 - actual_bytes / before_bytes
    assert saved >= 0.40, f"only saved {saved:.0%} ({before_bytes} -> {actual_bytes} bytes)"


def test_no_container_item_carries_content_beyond_its_title(client):
    # The durable form of the size assertion: regardless of percentages, a
    # container listing must not echo any of its body back.
    marker = "qqqdistinctivebodyqqq"
    _note(client, content=f"# Container\n\n{marker}", kind="container")

    body = client.get("/api/v1/notes?scope=all&limit=100").text
    assert marker not in body


# --- breadcrumb on search hits (Reqs 1, 2, 3) ------------------------------


def _tree(client):
    """School > UH-100 > US History Foundations, with an essay at the leaf."""
    school = _container(client, "School")
    uh100 = _container(client, "UH-100")
    foundations = _container(client, "US History Foundations")
    essay = _note(client, "# Reconstruction essay\n\nzzyzxterm midterm draft.")
    for child, parent in ((uh100, school), (foundations, uh100), (essay, foundations)):
        client.put(f"/api/v1/notes/{child}/parent", json={"parent_id": parent})
    return school, uh100, foundations, essay


def test_keyword_search_hit_carries_the_full_breadcrumb(client):
    school, uh100, foundations, essay = _tree(client)

    hit = client.get("/api/v1/search?q=zzyzxterm").json()["items"][0]

    assert hit["id"] == essay
    assert hit["kind"] == "note"
    assert hit["parent_id"] == foundations
    assert hit["breadcrumb"] == "School › UH-100 › US History Foundations"


def test_hybrid_search_hit_carries_the_full_breadcrumb(client, monkeypatch):
    import search as search_module

    school, uh100, foundations, essay = _tree(client)
    monkeypatch.setattr(search_module, "embed_text", lambda q: [0.1])
    monkeypatch.setattr(
        search_module, "search_vectors",
        lambda vector, limit: [{"id": "c1", "score": 0.9, "payload": {"note_id": essay}}],
    )

    hit = client.get("/api/v1/search/hybrid?q=zzyzxterm").json()["items"][0]

    assert hit["id"] == essay
    assert hit["parent_id"] == foundations
    assert hit["breadcrumb"] == "School › UH-100 › US History Foundations"


def test_search_hit_for_a_top_level_note_has_an_empty_breadcrumb(client):
    _note(client, "# Loose\n\nzzyzxterm with no container above it.")

    hit = client.get("/api/v1/search?q=zzyzxterm").json()["items"][0]

    assert "breadcrumb" in hit, "the key must be present, not omitted"
    assert hit["breadcrumb"] == ""
    assert hit["parent_id"] is None


def test_a_searched_container_reports_the_chain_above_itself(client):
    school, uh100, _, _ = _tree(client)

    hit = client.get("/api/v1/search?q=UH-100&scope=containers").json()["items"][0]

    assert hit["id"] == uh100
    assert hit["kind"] == "container"
    assert hit["parent_id"] == school
    assert hit["breadcrumb"] == "School", "a container's breadcrumb excludes itself"


def test_breadcrumb_survives_a_dangling_parent(client):
    # parent_id has no foreign key, so this state is reachable.
    note_id = _note(client, "# Orphan\n\nzzyzxterm here.")
    with connect() as db:
        db.execute("UPDATE notes SET parent_id = 'ghost' WHERE id = ?", (note_id,))

    hit = client.get("/api/v1/search?q=zzyzxterm").json()["items"][0]

    assert hit["breadcrumb"] == ""
    assert hit["parent_id"] == "ghost"


# --- review pass 1 regressions ---------------------------------------------


def test_an_empty_parent_body_is_rejected_rather_than_unfiling(client):
    # `{}` used to mean "clear the parent", so a request that forgot the
    # field silently unfiled an already-filed note.
    container_id = _container(client, "Box")
    note_id = _note(client)
    client.put(f"/api/v1/notes/{note_id}/parent", json={"parent_id": container_id})

    res = client.put(f"/api/v1/notes/{note_id}/parent", json={})

    assert res.status_code == 422
    assert client.get(f"/api/v1/notes/{note_id}/meta").json()["parent_id"] == container_id


def test_clearing_a_parent_still_works_when_asked_for_explicitly(client):
    container_id = _container(client, "Box")
    note_id = _note(client)
    client.put(f"/api/v1/notes/{note_id}/parent", json={"parent_id": container_id})

    res = client.put(f"/api/v1/notes/{note_id}/parent", json={"parent_id": None})

    assert res.status_code == 200
    assert client.get(f"/api/v1/notes/{note_id}/meta").json()["parent_id"] is None


def test_children_omits_container_previews_too(client):
    # children() is a listing, so the same rule applies there as in /notes.
    box = _container(client, "Box")
    sub = _note(client, content="# Sub\n\nA description that should not be echoed.",
                kind="container")
    note = _note(client, content="# Note\n\nOrdinary body.")
    for child in (sub, note):
        client.put(f"/api/v1/notes/{child}/parent", json={"parent_id": box})

    items = {i["id"]: i for i in client.get(f"/api/v1/notes/{box}/children").json()["items"]}

    assert items[sub]["preview"] == "", "a container's preview must be empty here too"
    assert items[sub]["title"] == "Sub", "the title still identifies it"
    assert "Ordinary body" in items[note]["preview"], "notes keep their preview"


def test_a_long_heading_gets_the_same_title_everywhere(client):
    # list_notes sliced content before deriving the title, so a heading longer
    # than the slice came out different from every other surface.
    heading = "H" * 300
    note_id = _note(client, content=f"# {heading}\n\nbody")

    listed = next(
        i for i in client.get("/api/v1/notes?limit=100").json()["items"]
        if i["id"] == note_id
    )
    searched = client.get(f"/api/v1/search?q={'H' * 300}").json()["items"]

    assert listed["title"] == heading[:120], "title is derived, then truncated"
    if searched:
        assert listed["title"] == searched[0]["title"], "surfaces must agree"


def test_many_heading_markers_do_not_empty_the_listed_title(client):
    note_id = _note(client, content=("#" * 150) + " Real Title\n\nbody")

    listed = next(
        i for i in client.get("/api/v1/notes?limit=100").json()["items"]
        if i["id"] == note_id
    )
    assert listed["title"] == "Real Title"
