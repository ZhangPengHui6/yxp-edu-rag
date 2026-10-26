"""Request/response models for ``/v1``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    query: str = ""
    messages: list[ChatMessage] = Field(default_factory=list)
    tenant_id: str | None = None
    subject: str = ""
    stage: str = ""
    grade: str = ""
    book_id: str = ""
    top_k: int | None = None


class Citation(BaseModel):
    citation_id: str
    source: Literal["chunk", "question"] = "chunk"
    chunk_id: str = ""
    question_id: str = ""
    book_id: str = ""
    book_title: str = ""
    subject: str = ""
    stage: str = ""
    question_type: str = ""
    difficulty: str = ""
    page_start: int = 0
    page_end: int = 0
    score: float = 0.0
    quote: str = ""


class RetrievalStats(BaseModel):
    dense: int = 0
    sparse: int = 0
    questions: int = 0
    fused: int = 0
    reranked: int = 0


class ChatResponse(BaseModel):
    request_id: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    model: str = ""
    rewritten_query: str = ""
    retrieval: RetrievalStats = Field(default_factory=RetrievalStats)
    timings_ms: dict[str, int] = Field(default_factory=dict)
    latency_ms: int = 0


class HealthResponse(BaseModel):
    status: str
    tenant: str
    chunks_alias: str
    chunks_alias_collection: str | None = None
    chunks: int = 0
    questions_alias: str
    questions: int = 0
    embedding_provider: str
    llm_configured: bool
    mongo_ok: bool
