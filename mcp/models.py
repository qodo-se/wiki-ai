"""Pydantic models for the wiki API's JSON shapes, mirroring cli/client.go's structs."""

from __future__ import annotations

from pydantic import BaseModel


class NoteSummary(BaseModel):
    id: str
    title: str
    path: str
    preview: str
    created_at: str
    # Every list item carries its tree position, so listing with
    # scope="containers" returns the whole skeleton in one call.
    kind: str = "note"
    parent_id: str | None = None


class NoteMeta(BaseModel):
    id: str
    title: str
    path: str
    created_at: str
    updated_at: str
    kind: str = "note"
    parent_id: str | None = None


class SearchHit(BaseModel):
    id: str
    title: str
    path: str
    preview: str
    score: float
    created_at: str


class NoteListResponse(BaseModel):
    items: list[NoteSummary]
    total: int
    limit: int
    offset: int


class SearchResponse(BaseModel):
    items: list[SearchHit]
    total: int
    limit: int
    offset: int


class AppConfig(BaseModel):
    ollama_url: str
    ollama_embedding_model: str
    ollama_chat_model: str
    qdrant_url: str
    qdrant_collection: str


class ReindexResult(BaseModel):
    total: int
    embedded: int
    failed: int


class UploadedImage(BaseModel):
    id: str
    url: str


class AncestorNode(BaseModel):
    """One container on the path from the root down to a note."""

    id: str
    title: str
    kind: str
    content: str
    content_truncated: bool


class AncestorsResponse(BaseModel):
    items: list[AncestorNode]


class ChildSummary(BaseModel):
    id: str
    title: str
    kind: str
    preview: str
    created_at: str


class ChildListResponse(BaseModel):
    items: list[ChildSummary]
    total: int
    limit: int
    offset: int
