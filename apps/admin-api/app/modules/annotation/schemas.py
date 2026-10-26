from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.core.schemas import CamelModel


class AnnotationTaskRowOut(CamelModel):
    id: str
    book: str
    chapter: str
    page: int
    pending: int
    total: int


class AnnotationBlockOut(CamelModel):
    id: str
    index: int
    type: Literal["标题", "正文", "公式", "表格", "图注"]
    content: str
    confidence: int
    status: Literal["pending", "resolved", "flagged"]


class AnnotationTaskOut(CamelModel):
    id: str
    book: str
    chapter: str
    page: int
    page_total: int
    cursor: int
    total: int
    blocks: list[AnnotationBlockOut]


class ResolveIn(CamelModel):
    block_id: str = Field(min_length=1)
    action: Literal["resolve", "flag"]
    content: str | None = None


class ResolveOut(CamelModel):
    resolved: int
    total: int
