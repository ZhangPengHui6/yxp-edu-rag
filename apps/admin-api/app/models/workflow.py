"""Workflow tables: ingestion, annotation, review and publish."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONType
from app.shared.time import utcnow


class IngestionJob(Base):
    __tablename__ = "ingestion_job"

    job_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    job_type: Mapped[str] = mapped_column(String(32), default="textbook")
    source_type: Mapped[str] = mapped_column(String(16), default="PDF")
    source_name: Mapped[str] = mapped_column(String(255))
    source_uri: Mapped[str] = mapped_column(String(512), default="")
    subject: Mapped[str] = mapped_column(String(32), default="")
    stage: Mapped[str] = mapped_column(String(16), default="middle")
    pages: Mapped[int] = mapped_column(Integer, default=0)
    size_mb: Mapped[float] = mapped_column(Float, default=0.0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    current_step: Mapped[str] = mapped_column(String(32), default="upload")
    stats: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    created_by: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IngestionJobStep(Base):
    __tablename__ = "ingestion_job_step"

    step_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), index=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    step: Mapped[str] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    detail: Mapped[str] = mapped_column(Text, default="")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnnotationTask(Base):
    __tablename__ = "annotation_task"

    task_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    book_id: Mapped[str] = mapped_column(String(40), index=True)
    book_title: Mapped[str] = mapped_column(String(255), default="")
    chapter_title: Mapped[str] = mapped_column(String(255), default="")
    page: Mapped[int] = mapped_column(Integer, default=1)
    page_total: Mapped[int] = mapped_column(Integer, default=0)
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(64), default="low_confidence")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    assignee_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolution: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewTask(Base):
    __tablename__ = "review_task"

    task_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    asset_type: Mapped[str] = mapped_column(String(32))
    asset_id: Mapped[str] = mapped_column(String(40), index=True)
    asset_version: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(16), default="题目")
    title: Mapped[str] = mapped_column(Text, default="")
    submitter_name: Mapped[str] = mapped_column(String(64), default="")
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    assignee_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PublishOrder(Base):
    __tablename__ = "publish_order"

    publish_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    target: Mapped[str] = mapped_column(String(32), default="production")
    scope: Mapped[str] = mapped_column(String(32), default="bank")
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    alias_chunks: Mapped[str] = mapped_column(String(64), default="")
    alias_questions: Mapped[str] = mapped_column(String(64), default="")
    index_version_old: Mapped[str] = mapped_column(String(64), default="")
    index_version_new: Mapped[str] = mapped_column(String(64), default="")
    versions: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(40), default="")
    created_by_name: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    staged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PublishItem(Base):
    __tablename__ = "publish_item"

    item_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    publish_id: Mapped[str] = mapped_column(String(40), index=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    asset_type: Mapped[str] = mapped_column(String(32))
    asset_id: Mapped[str] = mapped_column(String(40))
    asset_version: Mapped[int] = mapped_column(Integer, default=1)
    action: Mapped[str] = mapped_column(String(16), default="upsert")
    status: Mapped[str] = mapped_column(String(16), default="pending")
