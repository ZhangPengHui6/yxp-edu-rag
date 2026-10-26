"""Dashboard aggregation: real counts from PG and Mongo."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.infra.mongo import col
from app.models import IngestionJob, ReviewTask
from app.modules.dashboard.schemas import (
    DashboardMetricOut,
    DashboardSummaryOut,
    TodoItemOut,
    TrendPointOut,
)


async def _coverage(tenant_id: str) -> tuple[int, int]:
    nodes = [
        doc
        async for doc in col("knowledge_points").find(
            {"tenant_id": tenant_id, "status": {"$ne": "deprecated"}},
            {"_id": 1, "parent_id": 1},
        )
    ]
    parents = {doc.get("parent_id") for doc in nodes}
    leaves = [doc["_id"] for doc in nodes if doc["_id"] not in parents]
    linked: set[str] = set()
    async for question in col("questions").find(
        {"tenant_id": tenant_id, "review_status": "published"},
        {"knowledge_point_ids": 1},
    ):
        linked.update(question.get("knowledge_point_ids", []) or [])
    covered = sum(1 for leaf in leaves if leaf in linked)
    return covered, len(leaves)


async def summary(
    session: AsyncSession, *, user: CurrentUser
) -> DashboardSummaryOut:
    tenant_id = user.tenant_id

    pending_annotation = await col("annotation_blocks").count_documents(
        {"tenant_id": tenant_id, "annotation_task_id": {"$exists": True}, "status": {"$ne": "resolved"}}
    )
    pending_reviews = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ReviewTask)
                .where(
                    ReviewTask.tenant_id == tenant_id,
                    ReviewTask.status.in_({"pending", "in_review"}),
                )
            )
        ).scalar_one()
    )
    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_jobs = int(
        (
            await session.execute(
                select(func.count())
                .select_from(IngestionJob)
                .where(
                    IngestionJob.tenant_id == tenant_id,
                    IngestionJob.status == "succeeded",
                    IngestionJob.finished_at >= month_start,
                )
            )
        ).scalar_one()
    )
    question_count = await col("questions").count_documents(
        {"tenant_id": tenant_id, "review_status": {"$ne": "deprecated"}}
    )
    failed_jobs = int(
        (
            await session.execute(
                select(func.count())
                .select_from(IngestionJob)
                .where(
                    IngestionJob.tenant_id == tenant_id,
                    IngestionJob.status == "failed",
                )
            )
        ).scalar_one()
    )
    covered, total_kp = await _coverage(tenant_id)
    coverage_rate = covered / total_kp if total_kp else 0
    gap_count = max(total_kp - covered, 0)

    metrics = [
        DashboardMetricOut(
            key="annotation",
            label="待校对",
            value=str(pending_annotation),
            note="低置信度识别段落",
            tone="warning",
        ),
        DashboardMetricOut(
            key="review",
            label="待审核",
            value=str(pending_reviews),
            note="题目、知识点与教材",
            tone="info",
        ),
        DashboardMetricOut(
            key="ingested",
            label="本月入库",
            value=str(month_jobs),
            note=f"个资料任务 · {question_count} 题",
            tone="success",
        ),
        DashboardMetricOut(
            key="coverage",
            label="知识点覆盖率",
            value=f"{coverage_rate * 100:.0f}%",
            note=f"{covered} / {total_kp} 个知识点",
            tone="accent",
        ),
    ]
    todos = [
        TodoItemOut(
            id="todo_annotation",
            kind="annotation",
            title="低置信度内容积压",
            count=pending_annotation,
            tone="warning",
        ),
        TodoItemOut(
            id="todo_review",
            kind="review",
            title="待审核内容",
            count=pending_reviews,
            tone="info",
        ),
        TodoItemOut(
            id="todo_failed",
            kind="failed_job",
            title="失败入库任务",
            count=failed_jobs,
            tone="danger",
        ),
        TodoItemOut(
            id="todo_coverage",
            kind="coverage",
            title="知识点覆盖缺口",
            count=gap_count,
            tone="info",
        ),
    ]

    trend = await _trend(tenant_id)
    return DashboardSummaryOut(metrics=metrics, todos=todos, trend=trend)


async def _trend(tenant_id: str) -> list[TrendPointOut]:
    today = datetime.now(UTC).date()
    days = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    ingested: Counter = Counter()
    published: Counter = Counter()
    async for doc in col("questions").find(
        {"tenant_id": tenant_id}, {"created_at": 1, "published_at": 1}
    ):
        created = doc.get("created_at")
        if isinstance(created, datetime):
            key = (created if created.tzinfo else created.replace(tzinfo=UTC)).date()
            ingested[key] += 1
        published_at = doc.get("published_at")
        if isinstance(published_at, datetime):
            key = (
                published_at if published_at.tzinfo else published_at.replace(tzinfo=UTC)
            ).date()
            published[key] += 1
    return [
        TrendPointOut(
            date=day.strftime("%m-%d"),
            ingested=ingested.get(day, 0),
            published=published.get(day, 0),
        )
        for day in days
    ]
