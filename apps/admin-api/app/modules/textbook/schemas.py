from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.core.schemas import CamelModel

Stage = Literal["primary", "middle", "high"]


class ChapterOut(CamelModel):
    id: str
    no: int
    title: str
    page: int
    question_count: int


class LicenseOut(CamelModel):
    holder: str
    scope: str
    expires_at: str
    status: Literal["valid", "expiring", "none"]


class TextbookOut(CamelModel):
    id: str
    title: str
    edition: str
    subject: str
    stage: Stage
    chapters: list[ChapterOut]
    page_count: int
    status: Literal["draft", "reviewing", "published", "offline"]
    license: LicenseOut
    updated_at: str


class LicenseUpdateIn(CamelModel):
    holder: str = Field(min_length=1, max_length=128)
    scope: str = Field(default="内部教研使用", max_length=255)
    expires_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
