import re

from pydantic import BaseModel

from db import connect


class SearchHit(BaseModel):
    id: str
    title: str
    preview: str
    score: int
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

    pattern = f"%{_escape_like(q)}%"
    with connect() as db:
        rows = db.execute(
            "SELECT id, title, content, created_at FROM notes "
            "WHERE content LIKE ? ESCAPE '\\' COLLATE NOCASE "
            "ORDER BY created_at DESC",
            (pattern,),
        ).fetchall()

    q_lower = q.lower()
    hits = [
        SearchHit(
            id=row[0],
            title=row[1] or _derive_title(row[2]),
            preview=_build_snippet(row[2], q),
            score=row[2].lower().count(q_lower),
            created_at=row[3],
        )
        for row in rows
    ]
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]
