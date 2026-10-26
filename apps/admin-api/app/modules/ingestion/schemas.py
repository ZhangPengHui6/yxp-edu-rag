from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.core.schemas import CamelModel

Stage = Literal["primary", "middle", "high"]
SourceType = Literal["PDF", "DOCX", "IMAGE", "ZIP"]


class JobStepOut(CamelModel):
    key: str
    label: str
    status: Literal["done", "running", "pending", "failed"]
    detail: str | None = None


class IngestionJobOut(CamelModel):
    id: str
    name: str
    source_type: SourceType
    subject: str
    stage: Stage
    pages: int
    size_mb: float
    progress: int
    status: Literal["pending", "running", "succeeded", "failed", "canceled"]
    steps: list[JobStepOut]
    created_at: str
    error: str | None = None


class PresignIn(CamelModel):
    filename: str = Field(min_length=1, max_length=255)


class PresignOut(CamelModel):
    upload_url: str
    object_key: str
    expires_in: int


class CreateJobIn(CamelModel):
    filename: str = Field(min_length=1, max_length=255)
    object_key: str = Field(min_length=1, max_length=512)
    source_type: SourceType = "PDF"
    subject: str = Field(default="数学", max_length=32)
    stage: Stage = "middle"
    pages: int = Field(default=0, ge=0, le=100000)
    size_mb: float = Field(default=0, ge=0, le=10240)
