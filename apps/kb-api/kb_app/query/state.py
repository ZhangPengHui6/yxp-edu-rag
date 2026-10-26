"""LangGraph state shared by the query nodes."""

from __future__ import annotations

from typing import Any, TypedDict


class QueryState(TypedDict, total=False):
    # request
    request_id: str
    query: str
    messages: list[dict[str, str]]
    tenant_id: str
    subject: str
    stage: str
    grade: str
    book_id: str
    top_k: int

    # confirm
    rewritten_query: str

    # retrieve
    dense_hits: list[dict[str, Any]]
    sparse_hits: list[dict[str, Any]]
    question_dense_hits: list[dict[str, Any]]
    question_sparse_hits: list[dict[str, Any]]

    # fusion / rerank
    fused_hits: list[dict[str, Any]]
    reranked_hits: list[dict[str, Any]]
    rerank_provider: str

    # generate
    citations: list[dict[str, Any]]
    answer: str
    confidence: str
    model: str

    # observability
    timings: dict[str, int]
    error: str
