from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.core.schemas import CamelModel

Stage = Literal["primary", "middle", "high"]


class OptionOut(CamelModel):
    key: str
    text: str


class QuestionOut(CamelModel):
    id: str
    stem: str
    type: str
    difficulty: str
    knowledge_points: list[str]
    source: str
    stage: Stage
    subject: str
    status: Literal["draft", "reviewing", "published", "archived"]
    answer: str
    analysis: str
    options: list[OptionOut] | None = None
    updated_at: str


class OptionIn(CamelModel):
    key: str = Field(max_length=8)
    text: str = Field(max_length=2000)


class QuestionUpdateIn(CamelModel):
    stem: str | None = None
    type: str | None = None
    difficulty: str | None = None
    knowledge_points: list[str] | None = None
    source: str | None = None
    stage: Stage | None = None
    subject: str | None = None
    status: str | None = None
    answer: str | None = None
    analysis: str | None = None
    options: list[OptionIn] | None = None


class VariantOut(CamelModel):
    id: str
    stem: str
    difficulty: str
    status: Literal["draft"] = "draft"
    transform: str


class DuplicatePairOut(CamelModel):
    id: str
    score: float
    left: QuestionOut
    right: QuestionOut
    suggestion: str
