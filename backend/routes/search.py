from fastapi import APIRouter, Query

from search import SearchHit, search_notes

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.get("", response_model=list[SearchHit])
def search(
    q: str = Query("", description="Search query"),
    limit: int = Query(10, ge=1, le=100),
):
    return search_notes(q, limit)
