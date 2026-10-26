from __future__ import annotations

from typing import Literal

from app.core.schemas import CamelModel

Tone = Literal["success", "warning", "danger", "info", "accent", "muted"]


class PublishAliasOut(CamelModel):
    chunks: str
    questions: str


class PublishChangeOut(CamelModel):
    label: str
    value: str
    tone: Tone


class PublishCheckOut(CamelModel):
    label: str
    detail: str
    passed: bool


class PublishStageOut(CamelModel):
    key: str
    title: str
    description: str
    status: Literal["done", "running", "pending", "failed"]


class PublishOrderOut(CamelModel):
    id: str
    status: Literal["draft", "staged", "published", "failed", "rolled_back"]
    target: str
    created_at: str
    created_by: str
    alias: PublishAliasOut
    changes: list[PublishChangeOut]
    checks: list[PublishCheckOut]
    stages: list[PublishStageOut]
    note: str | None = None


class PublishCreateIn(CamelModel):
    target: str = "production"
    created_by: str | None = None
