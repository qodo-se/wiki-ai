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


def keyword_matches(query: str) -> list[SearchHit]:
    # Every matching note, ranked, with no pagination applied — used both by
    # the /api/v1/search route (which also needs a total count) and by
    # hybrid_search_notes (which wants the full candidate list to fuse), so
    # the word-boundary matching logic lives in exactly one place.
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
        count = sum(1 for _ in word_match.finditer(content))  # count only — finditer avoids materializing every match
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
    # id as a tiebreaker (unique per note) gives pagination a fully
    # deterministic order — ties on score alone could otherwise fall back to
    # SQLite's unspecified order for ties in "ORDER BY created_at", which
    # isn't guaranteed stable across requests and could shift a note across
    # a page boundary between one page load and the next.
    hits.sort(key=lambda h: (-h.score, h.id))
    return hits


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

# Caps how many notes' worth of candidates hybrid search ever fetches for
# fusion, regardless of how many notes actually exist — protects a
# surprisingly large wiki from one search request ranking everything.
_MAX_HYBRID_CANDIDATE_NOTES = 1000


def _fuse_rrf(ranked_lists: list[list]) -> list[HybridSearchHit]:
    # Every note found in any input list, fused by rank position (not raw
    # score — a keyword occurrence count and a cosine similarity aren't
    # comparable numbers) and sorted best-first. Unsliced — callers decide
    # how much of this to return, since the full length is also the total
    # for pagination.
    rrf_scores: dict[str, float] = {}
    hit_by_id: dict[str, SearchHit | SemanticSearchHit] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits):
            rrf_scores[hit.id] = rrf_scores.get(hit.id, 0.0) + 1.0 / (_RRF_K + rank + 1)
            hit_by_id.setdefault(hit.id, hit)

    # note_id as a tiebreaker gives a fully deterministic total order — the
    # same reason keyword_matches() sorts on (score, id), not score alone:
    # without it, a tie between two notes' fused scores could shift between
    # page requests and skip or duplicate a note across a page boundary.
    ranked_ids = sorted(rrf_scores, key=lambda note_id: (-rrf_scores[note_id], note_id))
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


def hybrid_search_notes(query: str, limit: int, offset: int = 0) -> tuple[list[HybridSearchHit], int]:
    # Keyword and semantic search alone each have a blind spot: a keyword
    # match misses a relevant note that doesn't use the query's exact words,
    # while a semantic match can bury a note that literally contains the
    # query term (whole-note embeddings don't privilege exact term overlap).
    #
    # Returns (page, total) rather than just a list: computing "total" by
    # re-running the fusion would mean embedding the query a second time,
    # which hits Ollama again for no reason — one fusion pass yields both.
    q = query.strip()
    if not q:
        return [], 0

    # Fetches a ranking over every note (capped, see above) rather than an
    # oversample scaled by the requested page, so "total" is exact and stable
    # across pages — if the candidate pool instead grew with limit/offset,
    # "total" would appear to shift as the user paged deeper. Cheap at this
    # app's realistic (personal-wiki-scale) data.
    with connect() as db:
        total_notes = db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    candidate_pool = min(total_notes, _MAX_HYBRID_CANDIDATE_NOTES)

    keyword_hits = keyword_matches(q)
    semantic_hits = semantic_search_notes(q, candidate_pool)
    fused = _fuse_rrf([keyword_hits, semantic_hits])
    return fused[offset : offset + limit], len(fused)
