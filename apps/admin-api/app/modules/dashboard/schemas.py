from __future__ import annotations

from typing import Literal

from app.core.schemas import CamelModel

Tone = Literal["success", "warning", "danger", "info", "accent", "muted"]


class DashboardMetricOut(CamelModel):
    key: str
    label: str
    value: str
    note: str
    tone: Tone


class TodoItemOut(CamelModel):
    id: str
    kind: Literal["annotation", "review", "failed_job", "coverage"]
    title: str
    count: int
    tone: Tone


class TrendPointOut(CamelModel):
    date: str
    ingested: int
    published: int


class DashboardSummaryOut(CamelModel):
    metrics: list[DashboardMetricOut]
    todos: list[TodoItemOut]
    trend: list[TrendPointOut]
