from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from embeddings import EmbeddingError
from reindex import ReindexInProgress, reindex_notes
from search import HybridSearchHit, hybrid_search_notes
from tree import Scope
from vectorstore import VectorStoreError

router = APIRouter(prefix="/api/v1/search", tags=["search"])


class SearchResponse(BaseModel):
    items: list[HybridSearchHit]
    total: int
    limit: int
    offset: int
    # True when the semantic half was unavailable and these results are a
    # keyword-only ranking. Clients use this both to tell the user and to
    # suppress any UI reading a fused score as a relevance percentage, since
    # a one-list fusion scores on a different scale than a two-list one.
    degraded: bool = False
    degraded_reason: str | None = None


@router.get("", response_model=SearchResponse)
def search(
    q: str = Query("", description="Search query"),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    scope: Scope = Query(Scope.notes, description="which kinds of note to search"),
):
    # The one search route: always keyword + semantic, fused by RRF. An
    # unreachable Ollama/Qdrant degrades to keyword-only results (flagged on
    # the response) rather than raising a 502, because there is no longer a
    # keyword-only mode for a caller to fall back to by hand.
    items, total, degraded_reason = hybrid_search_notes(q, limit, offset, scope)
    return SearchResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        degraded=degraded_reason is not None,
        degraded_reason=degraded_reason,
    )


@router.post("/reindex")
def reindex():
    try:
        return reindex_notes()
    except ReindexInProgress:
        raise HTTPException(status_code=409, detail="a reindex run is already in progress")
    except (EmbeddingError, VectorStoreError) as e:
        raise HTTPException(status_code=502, detail=str(e))
