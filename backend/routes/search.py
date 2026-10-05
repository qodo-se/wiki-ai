from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from embeddings import EmbeddingError
from reindex import ReindexInProgress, reindex_notes
from search import HybridSearchHit, SearchHit, hybrid_search_notes, keyword_matches
from vectorstore import VectorStoreError

router = APIRouter(prefix="/api/v1/search", tags=["search"])


class SearchResponse(BaseModel):
    items: list[SearchHit]
    total: int
    limit: int
    offset: int


class HybridSearchResponse(BaseModel):
    items: list[HybridSearchHit]
    total: int
    limit: int
    offset: int


@router.get("", response_model=SearchResponse)
def search(
    q: str = Query("", description="Search query"),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    all_hits = keyword_matches(q)
    return SearchResponse(items=all_hits[offset : offset + limit], total=len(all_hits), limit=limit, offset=offset)


@router.get("/hybrid", response_model=HybridSearchResponse)
def search_hybrid(
    q: str = Query("", description="Search query"),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    try:
        items, total = hybrid_search_notes(q, limit, offset)
        return HybridSearchResponse(items=items, total=total, limit=limit, offset=offset)
    except (EmbeddingError, VectorStoreError) as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/reindex")
def reindex():
    try:
        return reindex_notes()
    except ReindexInProgress:
        raise HTTPException(status_code=409, detail="a reindex run is already in progress")
    except (EmbeddingError, VectorStoreError) as e:
        raise HTTPException(status_code=502, detail=str(e))
