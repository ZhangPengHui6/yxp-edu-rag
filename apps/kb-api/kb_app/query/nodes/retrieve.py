"""Hybrid retrieval node: chunks and questions, dense + sparse each."""

from __future__ import annotations

import time
from typing import Any

from kb_app.config import get_kb_settings
from kb_app.infra.retrieval import hybrid_retrieve
from kb_app.query.state import QueryState


async def retrieve_node(state: QueryState) -> dict[str, Any]:
    started = time.perf_counter()
    settings = get_kb_settings()
    top_k = int(state.get("top_k") or settings.kb_top_k)
    dense, sparse, question_dense, question_sparse = await hybrid_retrieve(
        state["rewritten_query"],
        tenant_id=state["tenant_id"],
        subject=state.get("subject", ""),
        stage=state.get("stage", ""),
        grade=state.get("grade", ""),
        book_id=state.get("book_id", ""),
        top_k=top_k,
    )
    return {
        "dense_hits": dense,
        "sparse_hits": sparse,
        "question_dense_hits": question_dense,
        "question_sparse_hits": question_sparse,
        "timings": {
            **(state.get("timings") or {}),
            "retrieve": int((time.perf_counter() - started) * 1000),
        },
    }
