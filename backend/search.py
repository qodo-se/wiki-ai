import re

from pydantic import BaseModel

from db import connect
from embeddings import embed_text
from vectorstore import search_vectors


class SearchHit(BaseModel):
    id: str
    title: str
    path: str
    preview: str
    score: int
    created_at: str


class SemanticSearchHit(BaseModel):
    id: str
    title: str
    path: str
    preview: str
    score: float
    created_at: str


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _derive_title(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return re.sub(r"^#+\s*", "", stripped)[:120]
    return ""


def _build_snippet(content: str, query: str, context: int = 80) -> str:
    idx = content.lower().find(query.lower())
    if idx < 0:
        snippet = content[:160]
        truncated = len(content) > 160
        snippet = re.sub(r"\s+", " ", snippet).strip()
        return snippet + ("…" if truncated else "")

    start = max(0, idx - context)
    end = min(len(content), idx + len(query) + context)
    snippet = re.sub(r"\s+", " ", content[start:end]).strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet = snippet + "…"
    return snippet


def search_notes(query: str, limit: int) -> list[SearchHit]:
    q = query.strip()
    if not q:
        return []

    # SQL does a cheap substring pre-filter (a superset of what we actually
    # want); the word-boundary regex below is what decides real matches and
    # the score, so a bare "car" doesn't count "career" as a hit. Lookaround
    # (not \b) because \b requires a word/non-word *transition* on each side —
    # it fails for a query like "C++" where the char right after the match
    # ("+") and the char after that (e.g. a space) are both non-word, so no
    # transition ever occurs there even though "C++" is clearly whole-word.
    pattern = f"%{_escape_like(q)}%"
    word_match = re.compile(r"(?<!\w)" + re.escape(q) + r"(?!\w)", re.IGNORECASE)
    with connect() as db:
        rows = db.execute(
            "SELECT id, title, path, content, created_at FROM notes "
            "WHERE content LIKE ? ESCAPE '\\' COLLATE NOCASE "
            "ORDER BY created_at DESC",
            (pattern,),
        ).fetchall()

    hits = []
    for row in rows:
        content = row[3]
        count = len(word_match.findall(content))
        if count == 0:
            continue  # substring matched, but not as a whole word
        hits.append(
            SearchHit(
                id=row[0],
                title=row[1] or _derive_title(content),
                path=row[2],
                preview=_build_snippet(content, q),
                score=count,
                created_at=row[4],
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


# A note now embeds as several chunk-points (see chunking.py) rather than
# one, so a note-level result list needs more raw chunk hits from Qdrant than
# the number of distinct notes we actually want back.
_CHUNK_OVERSAMPLE_FACTOR = 8
_MIN_CHUNK_CANDIDATES = 100


def semantic_search_notes(query: str, limit: int) -> list[SemanticSearchHit]:
    # Not exposed as its own API route — a benchmark comparing keyword,
    # semantic, hybrid, and LLM-expanded hybrid search (recall@5/@10, MRR,
    # latency, across 11 hand-labeled queries) showed hybrid_search_notes
    # beats plain semantic on every quality metric at the same latency, so
    # this is kept only as the semantic half hybrid fuses with keyword search.
    q = query.strip()
    if not q:
        return []

    vector = embed_text(q)
    chunk_pool = max(limit * _CHUNK_OVERSAMPLE_FACTOR, _MIN_CHUNK_CANDIDATES)
    matches = search_vectors(vector, chunk_pool)
    if not matches:
        return []

    # Collapse chunk-level hits to one best score per note — a note's
    # relevance is however well its single best-matching chunk scores, not an
    # average across all its chunks.
    best_score_by_note: dict[str, float] = {}
    for m in matches:
        note_id = m.get("payload", {}).get("note_id")
        if note_id is None:
            continue  # a point from before chunking (no note_id payload) — ignore until reindexed
        if note_id not in best_score_by_note or m["score"] > best_score_by_note[note_id]:
            best_score_by_note[note_id] = m["score"]
    ranked_note_ids = sorted(best_score_by_note, key=lambda nid: best_score_by_note[nid], reverse=True)[:limit]
    if not ranked_note_ids:
        return []

    placeholders = ",".join("?" * len(ranked_note_ids))
    with connect() as db:
        rows = db.execute(
            f"SELECT id, title, path, content, created_at FROM notes WHERE id IN ({placeholders})",
            ranked_note_ids,
        ).fetchall()
    notes_by_id = {row[0]: row for row in rows}

    hits = []
    for note_id in ranked_note_ids:
        row = notes_by_id.get(note_id)
        if row is None:
            continue  # chunks for a note that's since been deleted
        _, title, path, content, created_at = row
        hits.append(
            SemanticSearchHit(
                id=row[0],
                title=title or _derive_title(content),
                path=path,
                preview=_build_snippet(content, q),
                score=best_score_by_note[note_id],
                created_at=created_at,
            )
        )
    return hits


class HybridSearchHit(BaseModel):
    id: str
    title: str
    path: str
    preview: str
    score: float
    created_at: str


# Reciprocal Rank Fusion constant from the original RRF paper (Cormack et al.,
# 2009): large enough that a note ranked #1 in only one of the two lists
# doesn't automatically outrank one ranked respectably in both.
_RRF_K = 60

# Each underlying search is asked for at least this many candidates, scaled
# up with the caller's requested `limit` so fusion always has more to work
# with than what's actually being asked for (otherwise a `limit` close to or
# above this floor could get fewer distinct notes back than requested, even
# when more genuinely matching notes exist).
_RRF_CANDIDATE_POOL_FLOOR = 50
_RRF_CANDIDATE_OVERSAMPLE_FACTOR = 3


def _fuse_rrf(ranked_lists: list[list], limit: int) -> list[HybridSearchHit]:
    # Fusing by rank position (not raw score — a keyword occurrence count and
    # a cosine similarity aren't comparable numbers) means a note either
    # input ranking was confident about still surfaces.
    rrf_scores: dict[str, float] = {}
    hit_by_id: dict[str, SearchHit | SemanticSearchHit] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits):
            rrf_scores[hit.id] = rrf_scores.get(hit.id, 0.0) + 1.0 / (_RRF_K + rank + 1)
            hit_by_id.setdefault(hit.id, hit)

    ranked_ids = sorted(rrf_scores, key=lambda note_id: rrf_scores[note_id], reverse=True)[:limit]
    return [
        HybridSearchHit(
            id=hit_by_id[note_id].id,
            title=hit_by_id[note_id].title,
            path=hit_by_id[note_id].path,
            preview=hit_by_id[note_id].preview,
            score=rrf_scores[note_id],
            created_at=hit_by_id[note_id].created_at,
        )
        for note_id in ranked_ids
    ]


def hybrid_search_notes(query: str, limit: int) -> list[HybridSearchHit]:
    # Keyword and semantic search alone each have a blind spot: a keyword
    # match misses a relevant note that doesn't use the query's exact words,
    # while a semantic match can bury a note that literally contains the
    # query term (whole-note embeddings don't privilege exact term overlap).
    q = query.strip()
    if not q:
        return []

    candidate_pool = max(limit * _RRF_CANDIDATE_OVERSAMPLE_FACTOR, _RRF_CANDIDATE_POOL_FLOOR)
    keyword_hits = search_notes(q, candidate_pool)
    semantic_hits = semantic_search_notes(q, candidate_pool)
    return _fuse_rrf([keyword_hits, semantic_hits], limit)
