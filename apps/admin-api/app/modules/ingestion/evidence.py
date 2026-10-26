"""Evidence scoring: rerank source chunks against every extracted question.

The reranker gets a small candidate set (same page first, then character-bigram
overlap) and returns relevance scores. The best chunks are stored on the question
as ``evidence_chunk_ids`` / ``evidence_score`` so reviewers can jump to the page
that supports the answer.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.infra.providers import RerankProvider
from app.modules.ingestion.extract import ExtractedQuestion

log = get_logger("evidence")

_SPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class Evidence:
    chunk_ids: list[str]
    score: float


def _bigrams(text: str) -> set[str]:
    clean = _SPACE_RE.sub("", text)
    if len(clean) < 2:
        return {clean} if clean else set()
    return {clean[index : index + 2] for index in range(len(clean) - 1)}


def _page_distance(page: int, chunk: dict[str, Any]) -> int:
    start = int(chunk.get("page") or 0)
    end = int(chunk.get("page_end") or start)
    if not page or not start:
        return 999
    if start <= page <= end:
        return 0
    return min(abs(page - start), abs(page - end))


def _candidates(
    question: ExtractedQuestion, chunks: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    grams = _bigrams(question.stem)
    ranked: list[tuple[int, float, int, dict[str, Any]]] = []
    for position, chunk in enumerate(chunks):
        content = str(chunk.get("content") or "")
        overlap = len(grams & _bigrams(content[:600])) / max(1, len(grams))
        ranked.append((_page_distance(question.page, chunk), -overlap, position, chunk))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    return [chunk for *_, chunk in ranked[:limit]]


async def rerank_evidence(
    questions: list[ExtractedQuestion],
    chunks: list[dict[str, Any]],
    reranker: RerankProvider,
) -> dict[int, Evidence]:
    """Return ``{question_index: Evidence}``; failures are logged and skipped."""
    if not questions or not chunks or reranker.name == "unconfigured":
        return {}
    limit = min(len(questions), max(0, settings.rerank_max_questions))
    if limit == 0:
        return {}
    semaphore = asyncio.Semaphore(max(1, settings.rerank_concurrency))
    top_n = max(1, settings.rerank_top_evidence)

    async def one(index: int) -> tuple[int, Evidence | None]:
        question = questions[index]
        candidates = _candidates(question, chunks, max(1, settings.rerank_candidates))
        documents = [str(chunk.get("content") or "")[: settings.rerank_max_chars] for chunk in candidates]
        if not any(documents):
            return index, None
        async with semaphore:
            scored = await reranker.rerank_with_scores(
                question.stem, documents, top_n=min(top_n, len(documents))
            )
        picked = [
            (position, score)
            for position, score in scored
            if 0 <= position < len(candidates) and score >= settings.rerank_min_score
        ]
        if not picked:
            return index, None
        chunk_ids = [
            str(candidates[position]["_id"])
            for position, _ in picked
            if candidates[position].get("_id")
        ]
        if not chunk_ids:
            return index, None
        return index, Evidence(chunk_ids=chunk_ids, score=round(max(score for _, score in picked), 4))

    results = await asyncio.gather(*(one(index) for index in range(limit)), return_exceptions=True)
    evidence: dict[int, Evidence] = {}
    failures = 0
    for item in results:
        if isinstance(item, BaseException):
            failures += 1
            continue
        index, value = item
        if value is not None:
            evidence[index] = value
    if failures:
        log.warning("rerank_partial_failure", questions=limit, failures=failures)
    return evidence
