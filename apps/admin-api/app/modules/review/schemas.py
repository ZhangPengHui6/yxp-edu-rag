from __future__ import annotations

from typing import Literal

from app.core.schemas import CamelModel


class ReviewPayloadOut(CamelModel):
    stem: str | None = None
    formula: str | None = None
    answer: str | None = None
    analysis: str | None = None
    knowledge_points: list[str] | None = None
    confidence: int | None = None
    source: str | None = None
    change_note: str | None = None


class ReviewTaskOut(CamelModel):
    id: str
    kind: Literal["题目", "知识点", "教材"]
    title: str
    submitter: str
    submitted_at: str
    priority: Literal["normal", "high"]
    payload: ReviewPayloadOut


class DecisionIn(CamelModel):
    decision: Literal["approve", "reject"]
    comment: str | None = None


class DecisionOut(CamelModel):
    id: str
    decision: str
    comment: str
