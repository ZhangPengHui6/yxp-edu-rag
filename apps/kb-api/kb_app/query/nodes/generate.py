"""Answer generation node and prompt assembly used by streaming too."""

from __future__ import annotations

import time
from typing import Any

from kb_app.config import get_kb_settings
from kb_app.infra.llm import generate
from kb_app.query.state import QueryState

SYSTEM_PROMPT = (
    "你是 K12 教材范围内的学习助教。"
    "只能依据【教材与题目资料】回答问题，不得编造资料中没有的内容、页码或出处。"
    "资料中的练习题及其答案只能作为参考，讲解时要说清推理过程，不能只抄答案。"
    "引用资料时在句末标注对应编号，例如 [1][2]。"
    "如果资料不足以回答，请直接说明缺少哪部分内容，并给出可继续追问的方向。"
    "回答使用中文，先给结论，再给推理或步骤，公式保持清晰。"
)


def history_messages(
    messages: list[dict[str, str]], *, current_query: str
) -> list[dict[str, str]]:
    """Keep recent turns, dropping a trailing copy of the current question."""
    limit = get_kb_settings().kb_history_turns * 2
    kept = [item for item in (messages or []) if item.get("role") in {"user", "assistant"}]
    if kept:
        last = kept[-1]
        if last.get("role") == "user" and (
            (last.get("content") or "").strip() == current_query.strip()
        ):
            kept = kept[:-1]
    return kept[-limit:]


def build_context(hits: list[dict[str, Any]]) -> str:
    settings = get_kb_settings()
    blocks: list[str] = []
    used = 0
    for index, hit in enumerate(hits, start=1):
        content = str(hit.get("content") or "").strip()
        if not content:
            continue
        if hit.get("source") == "question":
            label = "练习题"
            meta = " · ".join(
                part
                for part in (
                    str(hit.get("subject") or ""),
                    str(hit.get("stage") or ""),
                    f"难度 {hit.get('difficulty')}" if hit.get("difficulty") else "",
                )
                if part
            )
        else:
            label = f"《{hit.get('book_title') or '教材'}》"
            page = int(hit.get("page_start") or 0)
            meta = " · ".join(
                part
                for part in (
                    str(hit.get("subject") or ""),
                    str(hit.get("stage") or ""),
                    f"第 {page} 页" if page else "",
                )
                if part
            )
        block = f"[{index}] {label}{('（' + meta + '）') if meta else ''}\n{content}\n"
        if used + len(block) > settings.kb_max_context_chars:
            break
        blocks.append(block)
        used += len(block)
    return "\n".join(blocks)


def build_citations(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = get_kb_settings()
    citations: list[dict[str, Any]] = []
    for index, hit in enumerate(hits, start=1):
        content = str(hit.get("content") or "")
        citations.append(
            {
                "citation_id": f"c{index}",
                "source": "question" if hit.get("source") == "question" else "chunk",
                "chunk_id": str(hit.get("chunk_id") or ""),
                "question_id": str(hit.get("question_id") or ""),
                "book_id": str(hit.get("book_id") or ""),
                "book_title": str(hit.get("book_title") or ""),
                "subject": str(hit.get("subject") or ""),
                "stage": str(hit.get("stage") or ""),
                "question_type": str(hit.get("question_type") or ""),
                "difficulty": str(hit.get("difficulty") or ""),
                "page_start": int(hit.get("page_start") or 0),
                "page_end": int(hit.get("page_end") or hit.get("page_start") or 0),
                "score": round(float(hit.get("rerank_score", hit.get("rrf_score", 0.0))), 4),
                "quote": content[: settings.kb_max_evidence_chars],
            }
        )
    return citations


def confidence_for(hits: list[dict[str, Any]], provider: str) -> str:
    if not hits:
        return "low"
    if provider == "rrf-fallback":
        return "medium"
    top = float(hits[0].get("rerank_score", 0.0))
    if top >= 0.7:
        return "high"
    if top >= 0.4:
        return "medium"
    return "low"


async def generate_node(state: QueryState) -> dict[str, Any]:
    started = time.perf_counter()
    hits = state.get("reranked_hits") or []
    citations = build_citations(hits)

    if not hits:
        return {
            "answer": "当前知识库中没有检索到与该问题相关的内容，请换个问法，或确认对应教材是否已发布。",
            "citations": [],
            "confidence": "low",
            "model": "",
            "timings": {
                **(state.get("timings") or {}),
                "generate": int((time.perf_counter() - started) * 1000),
            },
        }

    answer, model = await generate(
        state["rewritten_query"],
        context=build_context(hits),
        history=history_messages(
            state.get("messages") or [], current_query=state["rewritten_query"]
        ),
        system_prompt=SYSTEM_PROMPT,
    )
    return {
        "answer": answer,
        "citations": citations,
        "confidence": confidence_for(hits, str(state.get("rerank_provider") or "")),
        "model": model,
        "timings": {
            **(state.get("timings") or {}),
            "generate": int((time.perf_counter() - started) * 1000),
        },
    }
