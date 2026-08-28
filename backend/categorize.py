import json
import re
import sys

from config import get_all_config
from db import connect
from embeddings import EmbeddingError
from llm import LLMError, generate_text
from vectorstore import VectorStoreError, scroll_all_points, search_vectors

PATH_CHARSET = re.compile(r"[^A-Za-z0-9 _./-]")
MAX_PATH_LENGTH = 200
MAX_REPRESENTATIVES_PER_CLUSTER = 5


class _UnionFind:
    def __init__(self, ids):
        self._parent = {i: i for i in ids}

    def find(self, x):
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]  # path halving
            x = self._parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

    def components(self):
        groups = {}
        for i in self._parent:
            groups.setdefault(self.find(i), []).append(i)
        return list(groups.values())


def _fetch_all_notes() -> dict[str, dict]:
    with connect() as db:
        rows = db.execute("SELECT id, content, path, title FROM notes").fetchall()
    return {r[0]: {"content": r[1], "path": r[2], "title": r[3]} for r in rows}


def _fetch_all_vectors() -> dict[str, list[float]]:
    points = scroll_all_points()
    vectors = {}
    for p in points:
        pid, vector = p.get("id"), p.get("vector")
        if isinstance(pid, str) and isinstance(vector, list):
            vectors[pid] = vector
    return vectors


def _top_k_neighbors(vectors: dict[str, list[float]], neighbor_limit: int) -> dict[str, list[str]]:
    top_k = {}
    for note_id, vector in vectors.items():
        # +1 to account for the note always matching itself as a top hit.
        matches = search_vectors(vector, neighbor_limit + 1)
        neighbor_ids = []
        for m in matches:
            other_id = m.get("id")
            if other_id == note_id or other_id not in vectors:
                continue
            neighbor_ids.append(other_id)
            if len(neighbor_ids) >= neighbor_limit:
                break
        top_k[note_id] = neighbor_ids
    return top_k


def _build_neighbor_graph(vectors: dict[str, list[float]], neighbor_limit: int) -> dict[str, set[str]]:
    # Mutual k-nearest-neighbors: connect two notes only if each is among the
    # other's top-K nearest neighbors. This depends only on each note's own
    # *relative* ranking of its neighbors, never on the absolute similarity
    # score — so it works the same regardless of which embedding model is
    # configured, even though different models produce wildly different
    # absolute cosine-similarity ranges for "genuinely similar" content.
    top_k = _top_k_neighbors(vectors, neighbor_limit)
    graph = {note_id: set() for note_id in vectors}
    for note_id, neighbor_ids in top_k.items():
        for other_id in neighbor_ids:
            if note_id in top_k.get(other_id, ()):
                graph[note_id].add(other_id)
                graph[other_id].add(note_id)
    return graph


def _cluster_ids(note_ids, graph: dict[str, set[str]]) -> list[list[str]]:
    uf = _UnionFind(note_ids)
    for note_id, neighbors in graph.items():
        for other_id in neighbors:
            uf.union(note_id, other_id)
    return uf.components()


def _sanitize_path(raw: str) -> str | None:
    candidate = raw.strip().splitlines()[0].strip() if raw and raw.strip() else ""
    candidate = candidate.strip("`\"' ")
    if not candidate:
        return None
    if not candidate.startswith("/"):
        candidate = "/" + candidate
    candidate = PATH_CHARSET.sub("", candidate)
    candidate = candidate.rstrip("/") or "/"
    candidate = candidate[:MAX_PATH_LENGTH]
    return candidate if candidate != "/" else None


def _representative_notes(cluster_ids: list[str], notes: dict[str, dict]) -> list[dict]:
    return [notes[i] for i in cluster_ids[:MAX_REPRESENTATIVES_PER_CLUSTER] if i in notes]


def _name_cluster(representatives: list[dict]) -> str | None:
    listing = "\n".join(
        f"- {(n['title'] or '(untitled)').strip()}: {n['content'][:200].strip()}"
        for n in representatives
    )
    prompt = (
        "You are organizing a personal wiki into folders. The notes below were "
        "grouped together because they're similar in topic:\n\n"
        f"{listing}\n\n"
        "Propose a short, lowercase folder path for this group, like \"/recipes\" "
        "or \"/tech/languages\". Respond with ONLY the path, nothing else."
    )
    try:
        raw = generate_text(prompt)
    except LLMError as e:
        print(f"warning: failed to name a cluster: {e}", file=sys.stderr)
        return None
    return _sanitize_path(raw)


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    for candidate in (text, re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _consolidate_names(proposed_names: list[str]) -> dict[str, str]:
    identity = {name: name for name in proposed_names}
    unique_names = sorted(set(proposed_names))
    if len(unique_names) < 2:
        return identity

    listing = "\n".join(f"- {name}" for name in unique_names)
    prompt = (
        "Here is a list of proposed folder paths for organizing a personal wiki:\n\n"
        f"{listing}\n\n"
        "Some may be near-duplicates (e.g. \"/recipes\" and \"/cooking\" should "
        "become one). Respond with ONLY a JSON object mapping each original path "
        "to its final, consolidated path. Every input path must appear as a key. "
        'Example: {"/recipes": "/recipes", "/cooking": "/recipes"}'
    )
    try:
        raw = generate_text(prompt)
    except LLMError as e:
        print(f"warning: failed to consolidate cluster names: {e}", file=sys.stderr)
        return identity

    parsed = _extract_json(raw)
    if parsed is None:
        print(f"warning: could not parse consolidation response: {raw!r}", file=sys.stderr)
        return identity

    result = dict(identity)
    for name in unique_names:
        final = parsed.get(name)
        if isinstance(final, str):
            sanitized = _sanitize_path(final)
            if sanitized:
                result[name] = sanitized
    return result


def reorganize_notes() -> dict:
    notes = _fetch_all_notes()
    if len(notes) < 2:
        return {"clusters": 0, "moved": 0, "unchanged": len(notes), "singletons": len(notes)}

    try:
        vectors = _fetch_all_vectors()
    except VectorStoreError as e:
        print(f"warning: could not fetch vectors for reorganize: {e}", file=sys.stderr)
        vectors = {}
    vectors = {note_id: v for note_id, v in vectors.items() if note_id in notes}

    cfg = get_all_config()
    neighbor_limit = int(cfg["categorize_neighbor_limit"])

    try:
        graph = _build_neighbor_graph(vectors, neighbor_limit)
    except (VectorStoreError, EmbeddingError) as e:
        print(f"warning: could not build neighbor graph for reorganize: {e}", file=sys.stderr)
        graph = {}

    clusters = _cluster_ids(list(vectors.keys()), graph)
    real_clusters = [c for c in clusters if len(c) >= 2]

    proposed_names: dict[int, str] = {}
    for idx, cluster in enumerate(real_clusters):
        name = _name_cluster(_representative_notes(cluster, notes))
        if name:
            proposed_names[idx] = name

    consolidated = _consolidate_names(list(proposed_names.values()))

    moved, unchanged = 0, 0
    updates = []
    for idx, cluster in enumerate(real_clusters):
        final_path = consolidated.get(proposed_names.get(idx, ""))
        if not final_path:
            unchanged += len(cluster)
            continue
        for note_id in cluster:
            if notes[note_id]["path"] == final_path:
                unchanged += 1
            else:
                updates.append((final_path, note_id))
                moved += 1

    if updates:
        with connect() as db:
            db.executemany(
                "UPDATE notes SET path = ?, updated_at = datetime('now') WHERE id = ?", updates
            )

    singletons = len(notes) - sum(len(c) for c in real_clusters)
    return {
        "clusters": len(real_clusters),
        "moved": moved,
        "unchanged": unchanged + singletons,
        "singletons": singletons,
    }
