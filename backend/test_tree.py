import pytest

import tree
from db import connect
from tree import InvalidParent, NoteNotFound, Scope


def _insert(note_id, content="body", kind=tree.KIND_NOTE, parent_id=None, created_at=None):
    with connect() as db:
        if created_at is None:
            db.execute(
                "INSERT INTO notes (id, content, kind, parent_id) VALUES (?, ?, ?, ?)",
                (note_id, content, kind, parent_id),
            )
        else:
            db.execute(
                "INSERT INTO notes (id, content, kind, parent_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (note_id, content, kind, parent_id, created_at),
            )


def _parent_of(note_id):
    with connect() as db:
        return db.execute("SELECT parent_id FROM notes WHERE id = ?", (note_id,)).fetchone()[0]


def _chain(*segments):
    """Builds a root-first container chain and returns the deepest id."""
    parent = None
    for seg in segments:
        _insert(seg, content=f"# {seg}", kind=tree.KIND_CONTAINER, parent_id=parent)
        parent = seg
    return parent


# --- scope_sql -------------------------------------------------------------


def test_scope_sql_is_always_a_complete_boolean_expression():
    # Scope.all has to be spliceable too, so no call site ever needs to branch
    # on the scope (branching is how a read surface forgets to filter).
    for scope in Scope:
        fragment, params = tree.scope_sql(scope)
        with connect() as db:
            db.execute(f"SELECT COUNT(*) FROM notes WHERE {fragment}", params)


def test_scope_sql_selects_the_right_kinds():
    _insert("n1", kind=tree.KIND_NOTE)
    _insert("c1", content="# C", kind=tree.KIND_CONTAINER)

    def ids_for(scope):
        fragment, params = tree.scope_sql(scope)
        with connect() as db:
            return {r[0] for r in db.execute(f"SELECT id FROM notes WHERE {fragment}", params)}

    assert ids_for(Scope.notes) == {"n1"}
    assert ids_for(Scope.containers) == {"c1"}
    assert ids_for(Scope.all) == {"n1", "c1"}


# --- ancestors -------------------------------------------------------------


def test_ancestors_returns_the_chain_root_first_excluding_the_note():
    _chain("education", "uh-100", "us-history")
    _insert("essay", parent_id="us-history")

    got = tree.ancestors("essay")

    assert [a["id"] for a in got] == ["education", "uh-100", "us-history"]
    assert all(a["kind"] == tree.KIND_CONTAINER for a in got)


def test_ancestors_carries_each_containers_prose():
    _insert(
        "uh-100",
        content="# UH-100\n\nA 100-level college survey course, 2018.",
        kind=tree.KIND_CONTAINER,
    )
    _insert("essay", parent_id="uh-100")

    (ancestor,) = tree.ancestors("essay")

    assert ancestor["title"] == "UH-100"
    assert "100-level college survey course" in ancestor["content"]
    assert ancestor["content_truncated"] is False


def test_ancestors_is_empty_for_a_top_level_note():
    _insert("loose")
    assert tree.ancestors("loose") == []


def test_ancestors_caps_long_container_bodies():
    body = "# Big\n\n" + ("x" * (tree.ANCESTOR_CONTENT_LIMIT + 500))
    _insert("big", content=body, kind=tree.KIND_CONTAINER)
    _insert("note", parent_id="big")

    (ancestor,) = tree.ancestors("note")

    assert len(ancestor["content"]) == tree.ANCESTOR_CONTENT_LIMIT
    assert ancestor["content_truncated"] is True


def test_ancestors_stops_cleanly_at_a_dangling_parent():
    # parent_id has no database-level FK, so this state is reachable.
    _insert("orphan", parent_id="does-not-exist")
    assert tree.ancestors("orphan") == []


def test_ancestors_returns_what_it_has_before_a_dangling_link():
    _insert("real", content="# Real", kind=tree.KIND_CONTAINER, parent_id="ghost")
    _insert("note", parent_id="real")

    got = tree.ancestors("note")

    assert [a["id"] for a in got] == ["real"]


def test_ancestors_does_not_spin_on_a_cycle_already_in_the_data():
    _insert("a", content="# A", kind=tree.KIND_CONTAINER)
    _insert("b", content="# B", kind=tree.KIND_CONTAINER, parent_id="a")
    with connect() as db:  # forge a cycle set_parent would have refused
        db.execute("UPDATE notes SET parent_id = 'b' WHERE id = 'a'")
    _insert("note", parent_id="b")

    got = tree.ancestors("note")

    assert len(got) <= tree.MAX_DEPTH
    assert {a["id"] for a in got} == {"a", "b"}


def test_ancestors_404s_on_an_unknown_note():
    with pytest.raises(NoteNotFound):
        tree.ancestors("nope")


# --- children --------------------------------------------------------------


def test_children_returns_direct_children_with_their_kinds():
    _insert("box", content="# Box", kind=tree.KIND_CONTAINER)
    _insert("sub", content="# Sub", kind=tree.KIND_CONTAINER, parent_id="box")
    _insert("n1", parent_id="box")
    _insert("n2", parent_id="box")
    _insert("elsewhere")

    items, total = tree.children("box", limit=10, offset=0)

    assert total == 3
    assert {i["id"] for i in items} == {"sub", "n1", "n2"}
    assert next(i for i in items if i["id"] == "sub")["kind"] == tree.KIND_CONTAINER


def test_children_orders_containers_first_then_newest():
    _insert("box", content="# Box", kind=tree.KIND_CONTAINER)
    _insert("old-note", parent_id="box", created_at="2020-01-01 00:00:00")
    _insert("new-note", parent_id="box", created_at="2024-01-01 00:00:00")
    _insert("sub", content="# Sub", kind=tree.KIND_CONTAINER, parent_id="box",
            created_at="2019-01-01 00:00:00")

    items, _ = tree.children("box", limit=10, offset=0)

    assert [i["id"] for i in items] == ["sub", "new-note", "old-note"]


def test_children_pages_without_changing_the_total():
    _insert("box", content="# Box", kind=tree.KIND_CONTAINER)
    for i in range(3):
        _insert(f"n{i}", parent_id="box", created_at=f"202{i}-01-01 00:00:00")

    items, total = tree.children("box", limit=2, offset=2)

    assert total == 3
    assert len(items) == 1


def test_children_of_a_regular_note_is_empty_not_an_error():
    _insert("plain")
    assert tree.children("plain", limit=10, offset=0) == ([], 0)


def test_children_404s_on_an_unknown_note():
    with pytest.raises(NoteNotFound):
        tree.children("nope", limit=10, offset=0)


# --- set_parent ------------------------------------------------------------


def test_set_parent_links_a_note_to_a_container():
    _insert("box", content="# Box", kind=tree.KIND_CONTAINER)
    _insert("note")

    tree.set_parent("note", "box")

    assert _parent_of("note") == "box"


def test_set_parent_none_clears_the_link():
    _insert("box", content="# Box", kind=tree.KIND_CONTAINER)
    _insert("note", parent_id="box")

    tree.set_parent("note", None)

    assert _parent_of("note") is None


def test_set_parent_allows_a_container_under_a_container():
    _insert("outer", content="# Outer", kind=tree.KIND_CONTAINER)
    _insert("inner", content="# Inner", kind=tree.KIND_CONTAINER)

    tree.set_parent("inner", "outer")

    assert _parent_of("inner") == "outer"


def test_set_parent_rejects_a_missing_parent():
    _insert("note", parent_id=None)
    with pytest.raises(InvalidParent, match="not found"):
        tree.set_parent("note", "ghost")
    assert _parent_of("note") is None


def test_set_parent_rejects_a_plain_note_as_parent():
    _insert("target")
    _insert("note")
    with pytest.raises(InvalidParent, match="not a container"):
        tree.set_parent("note", "target")
    assert _parent_of("note") is None


def test_set_parent_rejects_self_parenting():
    _insert("box", content="# Box", kind=tree.KIND_CONTAINER)
    with pytest.raises(InvalidParent, match="own parent"):
        tree.set_parent("box", "box")
    assert _parent_of("box") is None


def test_set_parent_rejects_a_cycle():
    _chain("a", "b", "c")  # a > b > c
    with pytest.raises(InvalidParent, match="cycle"):
        tree.set_parent("a", "c")  # would close the loop
    assert _parent_of("a") is None


def test_set_parent_rejects_exceeding_the_depth_limit():
    deepest = _chain(*[f"c{i}" for i in range(tree.MAX_DEPTH)])
    _insert("note")
    with pytest.raises(InvalidParent, match="limit"):
        tree.set_parent("note", deepest)
    assert _parent_of("note") is None


def test_set_parent_allows_the_last_slot_inside_the_depth_limit():
    deepest = _chain(*[f"c{i}" for i in range(tree.MAX_DEPTH - 1)])
    _insert("note")

    tree.set_parent("note", deepest)

    assert _parent_of("note") == deepest


def test_set_parent_404s_on_an_unknown_note():
    with pytest.raises(NoteNotFound):
        tree.set_parent("nope", None)


# --- promote_children ------------------------------------------------------


def test_promote_children_rehomes_onto_the_grandparent():
    _insert("a", content="# A", kind=tree.KIND_CONTAINER)
    _insert("b", content="# B", kind=tree.KIND_CONTAINER, parent_id="a")
    _insert("n1", parent_id="b")
    _insert("n2", parent_id="b")

    with connect() as db:
        tree.promote_children(db, "b", "a")

    assert _parent_of("n1") == "a"
    assert _parent_of("n2") == "a"


def test_promote_children_sends_them_to_top_level_when_there_is_no_grandparent():
    _insert("b", content="# B", kind=tree.KIND_CONTAINER)
    _insert("n1", parent_id="b")

    with connect() as db:
        tree.promote_children(db, "b", None)

    assert _parent_of("n1") is None


def test_promote_children_leaves_other_notes_alone():
    _insert("b", content="# B", kind=tree.KIND_CONTAINER)
    _insert("mine", parent_id="b")
    _insert("theirs")

    with connect() as db:
        tree.promote_children(db, "b", None)

    assert _parent_of("theirs") is None
    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 3


# --- review pass 2 regressions ---------------------------------------------


def test_children_checks_existence_inside_the_transaction():
    # The check used to run before BEGIN, so a delete committing in the gap
    # turned a 404 into a successful empty page for a note already gone.
    # Asserts the ordering directly: BEGIN must precede the existence SELECT.
    statements = []
    real_connect = tree.connect

    class RecordingConn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args):
            statements.append(sql.strip().split()[0].upper())
            return self._inner.execute(sql, *args)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

    tree.connect = lambda: RecordingConn(real_connect())
    try:
        _insert("box", content="# Box", kind=tree.KIND_CONTAINER)
        statements.clear()
        tree.children("box", limit=10, offset=0)
    finally:
        tree.connect = real_connect

    assert statements[0] == "BEGIN", f"expected BEGIN first, got {statements}"
    assert "SELECT" in statements[1:]


def test_children_404s_for_a_note_that_no_longer_exists():
    _insert("box", content="# Box", kind=tree.KIND_CONTAINER)
    with connect() as db:
        db.execute("DELETE FROM notes WHERE id = 'box'")

    with pytest.raises(NoteNotFound):
        tree.children("box", limit=10, offset=0)


def test_children_count_and_page_agree_under_the_same_snapshot():
    _insert("box", content="# Box", kind=tree.KIND_CONTAINER)
    for i in range(5):
        _insert(f"n{i}", parent_id="box")

    items, total = tree.children("box", limit=10, offset=0)

    assert total == len(items) == 5


def test_ancestors_walks_a_single_consistent_snapshot():
    # Each hop is its own SELECT; the read transaction is what stops them
    # landing on different committed states and splicing together a chain
    # that never simultaneously existed.
    _chain("a", "b", "c")
    _insert("note", parent_id="c")

    assert [x["id"] for x in tree.ancestors("note")] == ["a", "b", "c"]


# --- breadcrumb helpers ----------------------------------------------------


def test_breadcrumb_map_returns_only_containers_with_derived_titles():
    _insert("box", content="# Box\n\nbody", kind=tree.KIND_CONTAINER)
    _insert("sub", content="# Sub", kind=tree.KIND_CONTAINER, parent_id="box")
    _insert("note", content="# Not A Container", parent_id="box")

    m = tree.breadcrumb_map()

    assert set(m) == {"box", "sub"}
    assert m["box"] == ("Box", None)
    assert m["sub"] == ("Sub", "box")


def test_breadcrumb_joins_ancestor_titles_root_first():
    _chain("school", "uh-100", "foundations")
    m = tree.breadcrumb_map()

    assert tree.breadcrumb_for("foundations", m) == "school › uh-100 › foundations"


def test_breadcrumb_excludes_the_node_itself():
    # Called with a container's own parent_id, the result is the chain ABOVE
    # it — a container's breadcrumb never repeats its own name.
    _chain("school", "uh-100")
    m = tree.breadcrumb_map()

    assert tree.breadcrumb_for(m["uh-100"][1], m) == "school"


def test_breadcrumb_is_empty_string_for_a_top_level_node():
    assert tree.breadcrumb_for(None, tree.breadcrumb_map()) == ""


def test_breadcrumb_stops_at_a_dangling_parent():
    # parent_id has no FK, so this state is reachable.
    assert tree.breadcrumb_for("does-not-exist", tree.breadcrumb_map()) == ""


def test_breadcrumb_returns_what_it_has_before_a_dangling_link():
    _insert("real", content="# Real", kind=tree.KIND_CONTAINER, parent_id="ghost")
    assert tree.breadcrumb_for("real", tree.breadcrumb_map()) == "Real"


def test_breadcrumb_does_not_spin_on_a_cycle_already_in_the_data():
    _insert("a", content="# A", kind=tree.KIND_CONTAINER)
    _insert("b", content="# B", kind=tree.KIND_CONTAINER, parent_id="a")
    with connect() as db:  # forge a cycle set_parent would have refused
        db.execute("UPDATE notes SET parent_id = 'b' WHERE id = 'a'")

    got = tree.breadcrumb_for("b", tree.breadcrumb_map())

    assert got.count(tree.BREADCRUMB_SEPARATOR) < tree.MAX_DEPTH
    assert set(got.split(tree.BREADCRUMB_SEPARATOR)) == {"A", "B"}


def test_breadcrumb_is_bounded_by_max_depth():
    deepest = _chain(*[f"c{i}" for i in range(tree.MAX_DEPTH + 10)])
    got = tree.breadcrumb_for(deepest, tree.breadcrumb_map())
    assert len(got.split(tree.BREADCRUMB_SEPARATOR)) == tree.MAX_DEPTH


def test_breadcrumb_ignores_a_parent_that_is_not_a_container():
    # Only containers are in the map, so a note somehow set as a parent ends
    # the chain rather than appearing in it.
    _insert("plain", content="# Plain")
    assert tree.breadcrumb_for("plain", tree.breadcrumb_map()) == ""
