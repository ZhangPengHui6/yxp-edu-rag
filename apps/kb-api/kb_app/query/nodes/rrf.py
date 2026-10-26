"""Reciprocal Rank Fusion of the chunk and question result lists."""

from __future__ import annotations

import time
from typing import Any

from kb_app.config import get_kb_settings
from kb_app.query.state import QueryState


def _key(hit: dict[str, Any]) -> str:
    if hit.get("source") == "question":
        return f"question:{hit.get('question_id') or ''}"
    return f"chunk:{hit.get('chunk_id') or ''}"


async def rrf_node(state: QueryState) -> dict[str, Any]:
    """Fuse four channels by ``sum(weight / (k + rank))`` (Cormack et al.).

    Chunks own most of the candidate window because the textbook corpus is far
    larger. ``KB_QUESTION_QUOTA`` reserves a few slots for question hits so an
    exact stem match always reaches rerank; unused question slots flow back to
    chunks. Rerank then decides the final order.
    """
    started = time.perf_counter()
    settings = get_kb_settings()
    k = settings.kb_rrf_k
    question_weight = settings.kb_question_weight

    merged: dict[str, dict[str, Any]] = {}
    for channel, hits, weight in (
        ("chunk_dense", state.get("dense_hits") or [], 1.0),
        ("chunk_sparse", state.get("sparse_hits") or [], 1.0),
        ("question_dense", state.get("question_dense_hits") or [], question_weight),
        ("question_sparse", state.get("question_sparse_hits") or [], question_weight),
    ):
        for rank, hit in enumerate(hits, start=1):
            key = _key(hit)
            if key.endswith(":"):
                continue
            record = merged.setdefault(key, {**hit, "rrf_score": 0.0})
            record["rrf_score"] = float(record["rrf_score"]) + weight / (k + rank)
            record[f"{channel}_rank"] = rank
            record[f"{channel}_score"] = float(hit.get("score", 0.0))

    ranked = sorted(merged.values(), key=lambda item: float(item["rrf_score"]), reverse=True)
    total = settings.kb_fused_top_n
    question_quota = max(0, min(settings.kb_question_quota, total))
    chunks = [item for item in ranked if item.get("source") != "question"]
    questions = [item for item in ranked if item.get("source") == "question"]

    selected = chunks[: total - question_quota] + questions[:question_quota]
    if len(selected) < total:
        taken = {id(item) for item in selected}
        for item in ranked:
            if len(selected) >= total:
                break
            if id(item) not in taken:
                selected.append(item)
    selected.sort(key=lambda item: float(item["rrf_score"]), reverse=True)

    return {
        "fused_hits": selected,
        "timings": {
            **(state.get("timings") or {}),
            "rrf": int((time.perf_counter() - started) * 1000),
        },
    }
