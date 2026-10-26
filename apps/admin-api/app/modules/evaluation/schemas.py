from __future__ import annotations

from typing import Literal

from app.core.schemas import CamelModel


class EvalDatasetOut(CamelModel):
    id: str
    name: str
    question_count: int
    updated_at: str


class EvalRunOut(CamelModel):
    id: str
    dataset: str
    model: str
    recall: float
    accuracy: float
    status: Literal["passed", "failed", "running"]
    created_at: str


class EvalMetricOut(CamelModel):
    label: str
    value: float
    display: str


class EvalFailureOut(CamelModel):
    question: str
    reason: str
    tone: Literal["success", "warning", "danger", "info", "accent", "muted"]


class EvalReportOut(CamelModel):
    run_id: str
    created_at: str
    metrics: list[EvalMetricOut]
    failures: list[EvalFailureOut]


class EvalRunIn(CamelModel):
    dataset: str | None = None
    model: str | None = None
