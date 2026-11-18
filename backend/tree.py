"""The knowledge tree: container notes, primary-parent links, and the scope
filter that keeps containers out of ordinary note results.

A container is just a note with kind='container' — same table, same CRUD, same
editor, same embeddings, same backup. What makes it a container is that it's
meant to group other notes and to *describe what that grouping is* in its own
body, and that `kind` keeps it out of ordinary note listings and searches.

Every note has at most one parent (`notes.parent_id`), which must be a
container. NULL means top level, so the vault is a forest rather than a
single-rooted tree.
"""

import enum

from db import connect

KIND_NOTE = "note"
KIND_CONTAINER = "container"

# A note's parent chain is walked one row at a time, so an unbounded chain
# would be an unbounded number of queries. 32 is far past anything a personal
# wiki needs, and it lets both set_parent (which rejects assignments that would
# exceed it) and ancestors (which simply stops walking) stay trivially bounded.
MAX_DEPTH = 32

# Ancestors exist to be fed to a model as context, so the response has to stay
# a predictable size — a container with a very long body shouldn't be able to
# blow up the walk-up for every note beneath it.
ANCESTOR_CONTENT_LIMIT = 2000


class Scope(str, enum.Enum):
    """Which kinds of note a read surface should return.

    `notes` is the default everywhere: containers are structural and would be
    noise in ordinary results. Callers opt in to seeing them.
    """

    notes = "notes"
    containers = "containers"
    all = "all"


def scope_sql(scope: Scope) -> tuple[str, list]:
    """The single source of truth for 'which rows count as in scope'.

    Returns (sql_fragment, params) where the fragment is always a complete
    boolean expression — including for Scope.all — so every call site can
    splice it in unconditionally with `AND {fragment}` instead of branching on
    the scope itself. Branching is exactly how a read surface ends up quietly
    forgetting to filter, which is the one bug this feature can't afford
    (see test_container_isolation.py).
    """
    if scope == Scope.all:
        return "1 = 1", []
    if scope == Scope.containers:
        return "kind = ?", [KIND_CONTAINER]
    return "kind = ?", [KIND_NOTE]


class NoteNotFound(Exception):
    """No note with that id exists (routes turn this into a 404)."""


class InvalidParent(Exception):
    """The requested parent assignment isn't allowed (routes turn this into a
    400 carrying this exception's message)."""


def _derive_title_for(content: str) -> str:
    # Imported lazily: search.py imports Scope from this module, so importing
    # it at module scope here would be a cycle. Same technique db.py already
    # uses for paths.normalize_path, and for the same reason.
    from search import _derive_title

    return _derive_title(content)


def _fetch(db, note_id: str):
    return db.execute(
        "SELECT id, content, kind, parent_id FROM notes WHERE id = ?", (note_id,)
    ).fetchone()


def _require(db, note_id: str):
    row = _fetch(db, note_id)
    if row is None:
        raise NoteNotFound(f"note {note_id} not found")
    return row


def ancestors(note_id: str) -> list[dict]:
    """The chain of containers a note lives under, ordered root first and
    ending at the note's immediate parent. The note itself is not included.

    Returns [] for a top-level note. Stops cleanly rather than raising if the
    chain hits a parent_id whose row no longer exists — parent_id has no
    database-level foreign key (see the migration 4 comment in db.py), so a
    dangling pointer is a state this has to survive, not assume away.
    """
    chain = []
    with connect() as db:
        # One read transaction for the whole walk. Each hop is its own SELECT,
        # so without this they can land on different committed states: move a
        # container after its child link is read, then move that container's
        # old parent, and the chain returned can splice together links that
        # were never simultaneously ancestors of this note.
        db.execute("BEGIN")
        row = _require(db, note_id)
        current = row[3]
        seen = set()
        while current is not None and len(chain) < MAX_DEPTH:
            if current in seen:
                break  # pre-existing cycle in the data; don't spin on it
            seen.add(current)
            parent = _fetch(db, current)
            if parent is None:
                break  # dangling parent_id
            _, content, kind, grandparent = parent
            chain.append(
                {
                    "id": parent[0],
                    "title": _derive_title_for(content),
                    "kind": kind,
                    "content": content[:ANCESTOR_CONTENT_LIMIT],
                    "content_truncated": len(content) > ANCESTOR_CONTENT_LIMIT,
                }
            )
            current = grandparent
    chain.reverse()
    return chain


# Joins ancestor titles in a breadcrumb. U+203A rather than "/" so a
# breadcrumb is never mistaken for a `path` value — the two are different
# things and will sit next to each other in every search hit.
BREADCRUMB_SEPARATOR = " › "


def breadcrumb_map(db=None) -> dict[str, tuple[str, str | None]]:
    """Every container as {id: (title, parent_id)}, in one query.

    Only containers can be parents, so this is the complete set of nodes any
    breadcrumb can be built from — which is what lets a caller resolve a whole
    page of search hits without going back to the database per hit. A naive
    ancestors() walk per hit would be up to MAX_DEPTH queries times the number
    of hits; this is one, regardless.

    Takes an open connection when the caller has one. A search that reads its
    matching rows and then opens a second connection for this can straddle a
    tree move committing in between, and report a hit's old parent_id beside a
    breadcrumb built from the new hierarchy. Sharing the caller's connection
    keeps both reads on one snapshot.
    """
    sql = "SELECT id, content, parent_id FROM notes WHERE kind = ?"
    if db is not None:
        rows = db.execute(sql, (KIND_CONTAINER,)).fetchall()
    else:
        with connect() as own:
            rows = own.execute(sql, (KIND_CONTAINER,)).fetchall()
    return {r[0]: (_derive_title_for(r[1]), r[2]) for r in rows}


def breadcrumb_for(parent_id: str | None, containers: dict[str, tuple[str, str | None]]) -> str:
    """The titles of the containers above a note, root first, joined.

    Takes the note's own parent_id (not the note), so it works for notes and
    containers alike — a container's breadcrumb is the chain above it, not
    including itself. Returns "" for a top-level node, matching ancestors()
    returning [] rather than null.

    Walks the map from breadcrumb_map() rather than the database, and carries
    the same guards ancestors() does: a parent_id with no row ends the chain
    (there is no foreign key, so dangling pointers are reachable), a repeat
    visit breaks out of a pre-existing cycle, and MAX_DEPTH bounds it either
    way.
    """
    titles = []
    seen = set()
    current = parent_id
    while current is not None and len(titles) < MAX_DEPTH:
        if current in seen:
            break  # pre-existing cycle in the data; don't spin on it
        seen.add(current)
        entry = containers.get(current)
        if entry is None:
            break  # dangling parent_id, or a parent that isn't a container
        title, current = entry
        titles.append(title)
    titles.reverse()
    return BREADCRUMB_SEPARATOR.join(titles)


def children(note_id: str, limit: int, offset: int) -> tuple[list[dict], int]:
    """One level down: the notes and containers whose parent is note_id,
    newest first within each kind, containers before notes.

    Returns (page, total). Called on a regular note this is simply empty — a
    note can never be a parent, so that's an honest answer rather than an
    error.
    """
    with connect() as db:
        # BEGIN first, then check existence: checking outside the transaction
        # let a delete commit in between, turning what should be a 404 into a
        # successful empty page for a note that no longer exists. The same
        # snapshot also keeps the count and the page below agreeing with each
        # other, as in list_notes.
        db.execute("BEGIN")
        _require(db, note_id)
        total = db.execute(
            "SELECT COUNT(*) FROM notes WHERE parent_id = ?", (note_id,)
        ).fetchone()[0]
        rows = db.execute(
            "SELECT id, content, kind, created_at FROM notes WHERE parent_id = ? "
            # Containers first so structure reads before contents, then newest
            # first, then id as a final tiebreaker so paging can't shuffle a
            # row across a page boundary between two requests (the same
            # determinism argument as search.keyword_matches).
            "ORDER BY CASE kind WHEN ? THEN 0 ELSE 1 END, created_at DESC, id "
            "LIMIT ? OFFSET ?",
            (note_id, KIND_CONTAINER, limit, offset),
        ).fetchall()
    # A container's preview is suppressed here for the same reason it is in
    # list_notes: it would just repeat the title line and the start of the
    # description, and browsing the tree a level at a time is exactly when
    # that bulk is least useful. The key stays present and empty so the shape
    # never varies by row kind.
    items = [
        {
            "id": r[0],
            "title": _derive_title_for(r[1]),
            "kind": r[2],
            "preview": "" if r[2] == KIND_CONTAINER else r[1][:120],
            "created_at": r[3],
        }
        for r in rows
    ]
    return items, total


def set_parent(note_id: str, parent_id: str | None) -> None:
    """Point a note at its primary container, or clear it with None.

    Rejects, with InvalidParent: a parent that doesn't exist, a parent that
    isn't a container, the note itself, a parent that is already a descendant
    of the note (which would orphan a cycle out of the tree), and an
    assignment that would push the note past MAX_DEPTH.
    """
    with connect() as db:
        # BEGIN IMMEDIATE, not a plain read-then-write: python's sqlite3 only
        # opens a transaction when the first DML runs, so without this the
        # cycle/kind/depth checks below would read outside any transaction and
        # the UPDATE would open its own afterwards. Two concurrent moves could
        # then each validate against the pre-move state and both commit --
        # making A the parent of B and B the parent of A, a cycle neither call
        # could see. Taking the write lock up front serializes the whole
        # check-and-write, and also stops the proposed parent being deleted
        # between its existence check and the update that points at it.
        db.execute("BEGIN IMMEDIATE")
        _require(db, note_id)

        if parent_id is not None:
            if parent_id == note_id:
                raise InvalidParent("a note cannot be its own parent")
            parent = _fetch(db, parent_id)
            if parent is None:
                raise InvalidParent(f"parent {parent_id} not found")
            if parent[2] != KIND_CONTAINER:
                raise InvalidParent(
                    f"parent {parent_id} is a {parent[2]}, not a container — "
                    "only containers can hold other notes"
                )

            # One upward walk answers both remaining questions: whether
            # note_id appears above the proposed parent (a cycle), and how
            # deep the note would end up (chain length, plus the note itself).
            depth = 0
            seen = set()
            current = parent_id
            while current is not None:
                if current == note_id:
                    raise InvalidParent(
                        f"{parent_id} is already below {note_id}; that would make a cycle"
                    )
                if current in seen:
                    break  # pre-existing cycle above the parent; not this call's doing
                seen.add(current)
                depth += 1
                if depth >= MAX_DEPTH:
                    raise InvalidParent(
                        f"that would nest this note deeper than the {MAX_DEPTH}-level limit"
                    )
                row = _fetch(db, current)
                if row is None:
                    break  # dangling parent_id above us; chain ends here
                current = row[3]

        db.execute(
            "UPDATE notes SET parent_id = ?, updated_at = datetime('now') WHERE id = ?",
            (parent_id, note_id),
        )


def promote_children(db, note_id: str, new_parent_id: str | None) -> None:
    """Re-home a doomed note's children onto its own parent.

    Takes an open connection rather than opening its own, so the promotion and
    the delete that follows it commit as one transaction — a crash between the
    two would otherwise leave children pointing at a row that's already gone.

    Promotion rather than orphaning-to-root: deleting "UH-100" shouldn't throw
    everything that was under it to the top level, it should leave it wherever
    UH-100 itself lived.
    """
    db.execute(
        "UPDATE notes SET parent_id = ?, updated_at = datetime('now') WHERE parent_id = ?",
        (new_parent_id, note_id),
    )
