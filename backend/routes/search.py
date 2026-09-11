from fastapi import APIRouter, HTTPException, Query

from embeddings import EmbeddingError
from reindex import ReindexInProgress, reindex_notes
from search import SearchHit, SemanticSearchHit, search_notes, semantic_search_notes
from vectorstore import VectorStoreError

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.get("", response_model=list[SearchHit])
def search(
    q: str = Query("", description="Search query"),
    limit: int = Query(10, ge=1, le=100),
):
    return search_notes(q, limit)


@router.get("/semantic", response_model=list[SemanticSearchHit])
def search_semantic(
    q: str = Query("", description="Search query"),
    limit: int = Query(10, ge=1, le=100),
):
    try:
        return semantic_search_notes(q, limit)
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
