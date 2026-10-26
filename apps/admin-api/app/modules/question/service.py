"""Question use cases: search, update, variants and duplicate detection."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AppError, NotFound
from app.domain.question_rules import (
    difficulty_label,
    difficulty_value,
    find_duplicate_pairs,
    status_label,
    status_value,
    type_label,
    type_value,
)
from app.models import ReviewTask
from app.modules.audit import service as audit
from app.modules.question import repository as repo
from app.modules.question.schemas import (
    DuplicatePairOut,
    QuestionOut,
    QuestionUpdateIn,
    VariantOut,
)
from app.shared.ids import new_id
from app.shared.time import fmt_dt


def _to_out(doc: dict) -> QuestionOut:
    return QuestionOut(
        id=doc["_id"],
        stem=doc.get("stem", ""),
        type=type_label(doc.get("type", "")),
        difficulty=difficulty_label(doc.get("difficulty", "")),
        knowledge_points=doc.get("knowledge_points", []),
        source=doc.get("source", ""),
        stage=doc.get("stage", "middle"),  # type: ignore[arg-type]
        subject=doc.get("subject", ""),
        status=status_label(doc.get("review_status", "draft")),  # type: ignore[arg-type]
        answer=doc.get("answer", ""),
        analysis=doc.get("analysis", ""),
        options=doc.get("options"),
        updated_at=fmt_dt(doc.get("updated_at")),
    )


async def list_questions(
    *,
    tenant_id: str,
    page: int,
    page_size: int,
    status: str | None,
    type_label_value: str | None,
    difficulty_label_value: str | None,
    keyword: str | None,
) -> tuple[list[QuestionOut], int]:
    docs, total = await repo.list_questions(
        tenant_id,
        page=page,
        page_size=page_size,
        status=status_value(status),
        type_value=type_value(type_label_value),
        difficulty=difficulty_value(difficulty_label_value),
        keyword=keyword,
    )
    return [_to_out(doc) for doc in docs], total


async def get_question(tenant_id: str, question_id: str) -> QuestionOut:
    doc = await repo.get_question(tenant_id, question_id)
    if doc is None:
        raise NotFound("题目不存在")
    return _to_out(doc)


async def get_variants(tenant_id: str, question_id: str) -> list[VariantOut]:
    question = await repo.get_question(tenant_id, question_id)
    if question is None:
        raise NotFound("题目不存在")
    return [
        VariantOut(
            id=doc["_id"],
            stem=doc.get("stem", ""),
            difficulty=difficulty_label(doc.get("difficulty", "medium")),
            transform=doc.get("transform", ""),
        )
        for doc in await repo.get_variants(question_id)
    ]


async def get_duplicates(tenant_id: str) -> list[DuplicatePairOut]:
    docs = await repo.all_questions(tenant_id)
    pairs = find_duplicate_pairs([doc for doc in docs if doc.get("review_status") != "deprecated"])
    return [
        DuplicatePairOut(
            id=pair["id"],
            score=pair["score"],
            left=_to_out(pair["left"]),
            right=_to_out(pair["right"]),
            suggestion=pair["suggestion"],
        )
        for pair in pairs
    ]


async def update_question(
    session: AsyncSession,
    *,
    user: CurrentUser,
    question_id: str,
    payload: QuestionUpdateIn,
) -> QuestionOut:
    existing = await repo.get_question(user.tenant_id, question_id)
    if existing is None:
        raise NotFound("题目不存在")
    provided = payload.model_dump(exclude_unset=True)
    if not provided:
        return _to_out(existing)

    values: dict[str, Any] = {}
    if "stem" in provided:
        stem = (payload.stem or "").strip()
        if not stem:
            raise AppError("题干不能为空")
        values["stem"] = stem
    if "answer" in provided:
        values["answer"] = payload.answer or ""
    if "analysis" in provided:
        values["analysis"] = payload.analysis or ""
    if "source" in provided:
        values["source"] = payload.source or ""
    if "subject" in provided:
        values["subject"] = payload.subject or ""
    if "stage" in provided:
        values["stage"] = payload.stage
    if "type" in provided:
        values["type"] = type_value(payload.type)
    if "difficulty" in provided:
        values["difficulty"] = difficulty_value(payload.difficulty)
    if "options" in provided:
        values["options"] = [option.model_dump() for option in payload.options or []]
    if "knowledge_points" in provided:
        names = payload.knowledge_points or []
        lookup = await repo.knowledge_lookup(user.tenant_id, names)
        missing = [name for name in names if name not in lookup]
        if missing:
            raise AppError("知识点不存在", details={"missing": missing})
        values["knowledge_points"] = names
        values["knowledge_point_ids"] = [lookup[name] for name in names]

    old_status = existing.get("review_status", "draft")
    new_status = status_value(payload.status) if "status" in provided else None
    if new_status is not None:
        values["review_status"] = new_status

    updated = await repo.update_question(question_id, values)
    if updated is None:
        raise NotFound("题目不存在")

    if new_status == "reviewing" and old_status != "reviewing":
        session.add(
            ReviewTask(
                task_id=new_id("rev"),
                tenant_id=user.tenant_id,
                asset_type="question",
                asset_id=question_id,
                asset_version=int(updated.get("version", 1)),
                kind="题目",
                title=updated.get("stem", "")[:500],
                submitter_name=user.name,
                priority="normal",
                status="pending",
                payload={
                    "stem": updated.get("stem", ""),
                    "answer": updated.get("answer", ""),
                    "analysis": updated.get("analysis", ""),
                    "knowledgePoints": updated.get("knowledge_points", []),
                    "source": updated.get("source", ""),
                },
                created_by=user.id,
            )
        )

    audit.record(
        session,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_name=user.name,
        action="question.update",
        resource_type="question",
        resource_id=question_id,
        before={"status": old_status, "stem": existing.get("stem", "")[:200]},
        after={"status": updated.get("review_status"), "stem": updated.get("stem", "")[:200]},
    )
    if new_status == "reviewing" and old_status != "reviewing":
        audit.emit(
            session,
            tenant_id=user.tenant_id,
            event_type="review.requested",
            aggregate_type="question",
            aggregate_id=question_id,
            payload={"kind": "题目"},
        )
    await session.commit()
    return _to_out(updated)
