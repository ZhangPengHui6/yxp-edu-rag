"""Two-phase publish: stage -> commit, plus alias rollback."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.errors import InvalidState, NotFound
from app.domain.publish_rules import all_checks_passed, build_checks, build_stages
from app.infra.chunk_store import get_chunk_store
from app.infra.milvus import get_vector_index
from app.infra.mongo import col
from app.models import EvalRun, PublishOrder, ReviewTask
from app.modules.audit import service as audit
from app.modules.publish.indexer import INDEXABLE_REVIEW, build_version_index
from app.modules.publish.schemas import (
    PublishAliasOut,
    PublishChangeOut,
    PublishCheckOut,
    PublishCreateIn,
    PublishOrderOut,
    PublishStageOut,
)
from app.shared.ids import new_id
from app.shared.time import fmt_dt, utcnow

_ACTIVE_REVIEW = {"pending", "in_review"}


async def _indexable_question_count(tenant_id: str) -> int:
    """Questions the next release will contain (approved is promoted on commit)."""
    return await col("questions").count_documents(
        {"tenant_id": tenant_id, "review_status": {"$in": list(INDEXABLE_REVIEW)}}
    )


async def _chunk_count(tenant_id: str) -> int:
    return await get_chunk_store().count(tenant_id)


async def _published_book_count(tenant_id: str) -> int:
    return await col("textbooks").count_documents(
        {"tenant_id": tenant_id, "status": "published"}
    )


async def _latest_published(session: AsyncSession, tenant_id: str) -> PublishOrder | None:
    return (
        await session.execute(
            select(PublishOrder)
            .where(PublishOrder.tenant_id == tenant_id, PublishOrder.status == "published")
            .order_by(PublishOrder.published_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _latest_eval(session: AsyncSession, tenant_id: str) -> EvalRun | None:
    return (
        await session.execute(
            select(EvalRun)
            .where(EvalRun.tenant_id == tenant_id)
            .order_by(EvalRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _reference_check(tenant_id: str) -> tuple[bool, str]:
    node_ids = {
        doc["_id"]
        async for doc in col("knowledge_points").find(
            {"tenant_id": tenant_id, "status": {"$ne": "deprecated"}}, {"_id": 1}
        )
    }
    invalid = 0
    async for question in col("questions").find(
        {"tenant_id": tenant_id, "review_status": {"$ne": "deprecated"}},
        {"knowledge_point_ids": 1},
    ):
        for kp_id in question.get("knowledge_point_ids", []) or []:
            if kp_id not in node_ids:
                invalid += 1
    if invalid:
        return False, f"{invalid} 条题目知识点引用失效"
    for chunk in await get_chunk_store().references(tenant_id):
        for kp_id in chunk.get("knowledge_point_ids", []) or []:
            if kp_id not in node_ids:
                invalid += 1
    if invalid:
        return False, f"{invalid} 条段落知识点引用失效"
    return True, "全部题目与知识点引用有效"


async def _alias_check() -> tuple[bool, str]:
    current = await get_vector_index().current(settings.chunks_alias)
    if current is None:
        return True, "首次发布，提交时将创建别名"
    return True, f"别名可正常解析到 {current}"


async def _eval_check(session: AsyncSession, tenant_id: str) -> tuple[bool, str]:
    run = await _latest_eval(session, tenant_id)
    if run is None:
        return False, "尚未运行发布前评测"
    passed = run.recall >= settings.eval_recall_threshold and run.status != "failed"
    return (
        passed,
        f"Recall@10 {run.recall:.2f} · 阈值 {settings.eval_recall_threshold:.2f}",
    )


async def _collect_checks(
    session: AsyncSession,
    *,
    tenant_id: str,
    vector_detail: str,
    vector_ok: bool = True,
) -> list[dict[str, Any]]:
    reference_ok, reference_detail = await _reference_check(tenant_id)
    alias_ok, alias_detail = await _alias_check()
    eval_ok, eval_detail = await _eval_check(session, tenant_id)
    return build_checks(
        reference_ok=reference_ok,
        reference_detail=reference_detail,
        vector_ok=vector_ok,
        vector_detail=vector_detail,
        alias_ok=alias_ok,
        alias_detail=alias_detail,
        eval_ok=eval_ok,
        eval_detail=eval_detail,
    )


async def _build_changes(
    session: AsyncSession,
    tenant_id: str,
    *,
    last_published: PublishOrder | None,
    vector_count: int,
) -> list[dict[str, Any]]:
    if last_published and last_published.published_at:
        since = last_published.published_at
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
    else:
        since = datetime.now(UTC) - timedelta(days=30)
    new_questions = await col("questions").count_documents(
        {
            "tenant_id": tenant_id,
            "review_status": {"$in": list(INDEXABLE_REVIEW)},
            "updated_at": {"$gt": since},
        }
    )
    updated_kp = await col("knowledge_points").count_documents(
        {"tenant_id": tenant_id, "updated_at": {"$gt": since}}
    )
    new_books = await col("textbooks").count_documents(
        {
            "tenant_id": tenant_id,
            "status": "published",
            "updated_at": {"$gt": since},
        }
    )
    offline = await col("questions").count_documents(
        {"tenant_id": tenant_id, "review_status": "deprecated", "updated_at": {"$gt": since}}
    )
    return [
        {"label": "新增题目", "value": f"+{new_questions}", "tone": "success"},
        {"label": "更新知识点", "value": f"+{updated_kp}", "tone": "info"},
        {"label": "新增教材", "value": f"+{new_books}", "tone": "accent"},
        {"label": "下线内容", "value": f"−{offline}" if offline else "0", "tone": "danger"},
        {"label": "向量条目", "value": str(vector_count), "tone": "muted"},
    ]


def _next_version() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _to_out(order: PublishOrder) -> PublishOrderOut:
    vector_count = int((order.versions or {}).get("vectorCount", 0))
    stages = build_stages(
        order.status,
        vector_count=vector_count,
        target_alias=order.index_version_old,
    )
    return PublishOrderOut(
        id=order.publish_id,
        status=order.status,  # type: ignore[arg-type]
        target=order.target,
        created_at=fmt_dt(order.created_at),
        created_by=order.created_by_name,
        alias=PublishAliasOut(chunks=order.alias_chunks, questions=order.alias_questions),
        changes=[PublishChangeOut(**item) for item in (order.changes or [])],
        checks=[PublishCheckOut(**item) for item in (order.checks or [])],
        stages=[PublishStageOut(**item) for item in stages],
        note=order.note or None,
    )


async def list_orders(
    session: AsyncSession,
    *,
    tenant_id: str,
    page: int,
    page_size: int,
    status: str | None,
) -> tuple[list[PublishOrderOut], int]:
    filters = [PublishOrder.tenant_id == tenant_id]
    if status and status != "all":
        filters.append(PublishOrder.status == status)
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(PublishOrder).where(*filters)
            )
        ).scalar_one()
    )
    orders = (
        (
            await session.execute(
                select(PublishOrder)
                .where(*filters)
                .order_by(PublishOrder.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return [_to_out(order) for order in orders], total


async def get_order(
    session: AsyncSession, *, tenant_id: str, publish_id: str
) -> PublishOrderOut:
    order = await session.get(PublishOrder, publish_id)
    if order is None or order.tenant_id != tenant_id:
        raise NotFound("发布单不存在")
    return _to_out(order)


async def create_order(
    session: AsyncSession, *, user: CurrentUser, payload: PublishCreateIn
) -> PublishOrderOut:
    last = await _latest_published(session, user.tenant_id)
    vector_count = await _chunk_count(user.tenant_id) + await _indexable_question_count(
        user.tenant_id
    )
    version = _next_version()
    while (
        await session.execute(
            select(func.count())
            .select_from(PublishOrder)
            .where(
                PublishOrder.tenant_id == user.tenant_id,
                PublishOrder.index_version_new == version,
            )
        )
    ).scalar_one():
        version = f"{version}_2"

    checks = await _collect_checks(
        session,
        tenant_id=user.tenant_id,
        vector_detail=f"{vector_count} 条待写入 chunks_{version}",
    )
    changes = await _build_changes(
        session,
        user.tenant_id,
        last_published=last,
        vector_count=vector_count,
    )
    old_version = last.index_version_new if last else ""
    note = (
        f"提交后将把 {settings.chunks_alias} 与 {settings.questions_alias} 从 "
        f"{old_version or '未初始化'} 原子切换到 {version}；支持一键回滚。"
    )
    order = PublishOrder(
        publish_id=new_id("pub"),
        tenant_id=user.tenant_id,
        target=payload.target or "production",
        scope="full",
        status="draft",
        alias_chunks=f"chunks_{version}",
        alias_questions=f"questions_{version}",
        index_version_old=old_version,
        index_version_new=version,
        versions={"vectorCount": vector_count, "chunkCount": await _chunk_count(user.tenant_id)},
        checks=checks,
        changes=changes,
        note=note,
        created_by=user.id,
        created_by_name=user.name,
    )
    session.add(order)
    audit.record(
        session,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_name=user.name,
        action="publish.create",
        resource_type="publish_order",
        resource_id=order.publish_id,
        after={"target": order.target, "version": version},
    )
    await session.commit()
    return _to_out(order)


async def stage_order(
    session: AsyncSession, *, user: CurrentUser, publish_id: str
) -> PublishOrderOut:
    order = await session.get(PublishOrder, publish_id)
    if order is None or order.tenant_id != user.tenant_id:
        raise NotFound("发布单不存在")
    if order.status == "staged":
        return _to_out(order)
    if order.status != "draft":
        raise InvalidState("只有草稿发布单可以暂存", {"status": order.status})

    version = order.index_version_new
    build = await build_version_index(tenant_id=user.tenant_id, version=version)
    chunk_count = build.chunks
    question_count = build.questions
    vector = get_vector_index()
    staged_chunks = await vector.stage(f"chunks_{version}", chunk_count)
    staged_questions = await vector.stage(f"questions_{version}", question_count)
    vector_count = staged_chunks + staged_questions

    embedded = (
        f"（补算 {build.embedded_chunks} 条段落 / {build.embedded_questions} 道题目向量）"
        if build.embedded_chunks or build.embedded_questions
        else ""
    )
    checks = await _collect_checks(
        session,
        tenant_id=user.tenant_id,
        vector_detail=(
            f"{staged_chunks} / {chunk_count} 条段落、{staged_questions} / "
            f"{question_count} 条题目已写入 chunks_{version}{embedded}"
        ),
    )
    if not all(bool(item["passed"]) for item in checks if item["label"] in {"引用完整性", "别名可用性"}):
        order.status = "failed"
        order.error_message = "暂存前校验失败"
        order.checks = checks
        await session.commit()
        raise InvalidState("暂存前校验失败", {"checks": checks})

    order.status = "staged"
    order.staged_at = utcnow()
    order.checks = checks
    order.versions = {
        **(order.versions or {}),
        "vectorCount": vector_count,
        "chunkCount": chunk_count,
        "questionCount": question_count,
        "embeddedChunks": build.embedded_chunks,
        "embeddedQuestions": build.embedded_questions,
        "embeddingProvider": build.provider,
    }
    audit.record(
        session,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_name=user.name,
        action="publish.stage",
        resource_type="publish_order",
        resource_id=publish_id,
        after={"status": "staged", "vectorCount": vector_count},
    )
    audit.emit(
        session,
        tenant_id=user.tenant_id,
        event_type="publish.staged",
        aggregate_type="publish_order",
        aggregate_id=publish_id,
        payload={"version": version, "vectorCount": vector_count},
    )
    await session.commit()
    return _to_out(order)


async def commit_order(
    session: AsyncSession, *, user: CurrentUser, publish_id: str
) -> PublishOrderOut:
    order = await session.get(PublishOrder, publish_id)
    if order is None or order.tenant_id != user.tenant_id:
        raise NotFound("发布单不存在")
    if order.status == "published":
        return _to_out(order)
    if order.status != "staged":
        raise InvalidState("发布单尚未暂存", {"status": order.status})

    version = order.index_version_new
    vector_count = int((order.versions or {}).get("vectorCount", 0))
    checks = await _collect_checks(
        session,
        tenant_id=user.tenant_id,
        vector_detail=f"{vector_count} / {vector_count} 条已写入 chunks_{version}",
    )
    if not all_checks_passed(checks):
        order.status = "failed"
        order.error_message = "发布校验未通过"
        order.checks = checks
        await session.commit()
        raise InvalidState("发布校验未通过，请先处理未通过项", {"checks": checks})

    vector = get_vector_index()
    await vector.commit(settings.chunks_alias, f"chunks_{version}")
    await vector.commit(settings.questions_alias, f"questions_{version}")

    order.status = "published"
    order.published_at = utcnow()
    order.checks = checks
    order.error_message = ""
    now = utcnow()
    await col("questions").update_many(
        {"tenant_id": user.tenant_id, "review_status": "approved"},
        {"$set": {"review_status": "published", "published_at": now, "updated_at": now}},
    )
    await col("textbooks").update_many(
        {"tenant_id": user.tenant_id, "status": "reviewing"},
        {"$set": {"status": "published", "updated_at": now}},
    )
    audit.record(
        session,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_name=user.name,
        action="publish.commit",
        resource_type="publish_order",
        resource_id=publish_id,
        before={"status": "staged"},
        after={"status": "published", "version": version},
    )
    audit.emit(
        session,
        tenant_id=user.tenant_id,
        event_type="asset.published",
        aggregate_type="publish_order",
        aggregate_id=publish_id,
        payload={
            "version": version,
            "chunksAlias": settings.chunks_alias,
            "questionsAlias": settings.questions_alias,
        },
    )
    await session.commit()
    return _to_out(order)


async def rollback_order(
    session: AsyncSession, *, user: CurrentUser, publish_id: str
) -> PublishOrderOut:
    order = await session.get(PublishOrder, publish_id)
    if order is None or order.tenant_id != user.tenant_id:
        raise NotFound("发布单不存在")
    if order.status == "rolled_back":
        return _to_out(order)
    if order.status != "published":
        raise InvalidState("只有已发布的版本可以回滚", {"status": order.status})
    if not order.index_version_old:
        raise InvalidState("没有可回滚的历史版本")

    vector = get_vector_index()
    await vector.rollback(settings.chunks_alias, f"chunks_{order.index_version_old}")
    await vector.rollback(settings.questions_alias, f"questions_{order.index_version_old}")
    order.status = "rolled_back"
    order.rolled_back_at = utcnow()
    order.alias_chunks = f"chunks_{order.index_version_old}"
    order.alias_questions = f"questions_{order.index_version_old}"
    order.note = f"已回滚至 {order.index_version_old}；原发布内容保留，可重新发布。"
    audit.record(
        session,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_name=user.name,
        action="publish.rollback",
        resource_type="publish_order",
        resource_id=publish_id,
        before={"status": "published", "version": order.index_version_new},
        after={"status": "rolled_back", "version": order.index_version_old},
    )
    audit.emit(
        session,
        tenant_id=user.tenant_id,
        event_type="asset.rollback",
        aggregate_type="publish_order",
        aggregate_id=publish_id,
        payload={"version": order.index_version_old},
    )
    await session.commit()
    return _to_out(order)


async def pending_review_count(session: AsyncSession, tenant_id: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(ReviewTask)
                .where(ReviewTask.tenant_id == tenant_id, ReviewTask.status.in_(_ACTIVE_REVIEW))
            )
        ).scalar_one()
    )
