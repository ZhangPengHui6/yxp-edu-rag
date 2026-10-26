"""Rerank the fused candidates with the configured reranker.

If the reranker is unavailable or errors, retrieval still succeeds using the
RRF order; the response reports the fallback provider.
"""

from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger

from kb_app.config import get_kb_settings
from kb_app.infra.llm import rerank
from kb_app.query.state import QueryState

log = get_logger("kb.rerank")


async def rerank_node(state: QueryState) -> dict[str, Any]:
    started = time.perf_counter()
    settings = get_kb_settings()
    fused = state.get("fused_hits") or []
    provider = "rrf-fallback"

    if not fused:
        return {
            "reranked_hits": [],
            "rerank_provider": provider,
            "timings": {
                **(state.get("timings") or {}),
                "rerank": int((time.perf_counter() - started) * 1000),
            },
        }

    documents = [
        (str(hit.get("content") or ""))[: settings.kb_max_evidence_chars] for hit in fused
    ]
    try:
        scored, provider = await rerank(
            state["rewritten_query"], documents, top_n=settings.kb_rerank_top_n
        )
        if scored:
            hits = []
            for index, score in scored:
                item = dict(fused[index])
                item["rerank_score"] = float(score)
                hits.append(item)
        else:
            hits = [
                {**hit, "rerank_score": float(hit.get("rrf_score", 0.0))}
                for hit in fused[: settings.kb_rerank_top_n]
            ]
    except Exception as exc:  # noqa: BLE001 - rerank is best-effort
        log.warning("rerank_failed", error=str(exc))
        hits = [
            {**hit, "rerank_score": float(hit.get("rrf_score", 0.0))}
            for hit in fused[: settings.kb_rerank_top_n]
        ]

    return {
        "reranked_hits": hits,
        "rerank_provider": provider,
        "timings": {
            **(state.get("timings") or {}),
            "rerank": int((time.perf_counter() - started) * 1000),
        },
    }
