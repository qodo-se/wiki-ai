"""Pydantic models for the wiki API's JSON shapes, mirroring cli/client.go's structs."""

from __future__ import annotations

from pydantic import BaseModel


class NoteSummary(BaseModel):
    id: str
    title: str
    path: str
    preview: str
    created_at: str


class NoteMeta(BaseModel):
    id: str
    title: str
    path: str
    created_at: str
    updated_at: str


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
