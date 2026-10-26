"""Offline evaluation over knowledge-point coverage.

The POC computes a retrieval-quality proxy from the published bank: for each
gold case, whether any published question is linked to an expected knowledge
point, and how good those questions are. Production replaces ``_compute`` with
the recall/answer evaluator in ``workers/eval_tasks.py``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.errors import AppError, NotFound
from app.infra.chunk_store import get_chunk_store
from app.infra.mongo import col
from app.infra.pg import SessionLocal
from app.models import EvalCase, EvalDataset, EvalResult, EvalRun
from app.modules.audit import service as audit
from app.modules.evaluation.schemas import (
    EvalDatasetOut,
    EvalFailureOut,
    EvalMetricOut,
    EvalReportOut,
    EvalRunIn,
    EvalRunOut,
)
from app.shared.ids import new_id
from app.shared.time import fmt_dt, utcnow


def _run_out(run: EvalRun) -> EvalRunOut:
    return EvalRunOut(
        id=run.run_id,
        dataset=run.dataset_name,
        model=run.model,
        recall=round(run.recall, 2),
        accuracy=round(run.accuracy, 2),
        status=run.status,  # type: ignore[arg-type]
        created_at=fmt_dt(run.created_at),
    )


async def list_datasets(session: AsyncSession, tenant_id: str) -> list[EvalDatasetOut]:
    datasets = (
        (
            await session.execute(
                select(EvalDataset)
                .where(EvalDataset.tenant_id == tenant_id)
                .order_by(EvalDataset.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        EvalDatasetOut(
            id=dataset.dataset_id,
            name=dataset.name,
            question_count=dataset.question_count,
            updated_at=fmt_dt(dataset.updated_at),
        )
        for dataset in datasets
    ]


async def list_runs(
    session: AsyncSession, *, tenant_id: str, page: int, page_size: int
) -> tuple[list[EvalRunOut], int]:
    filters = [EvalRun.tenant_id == tenant_id]
    total = int(
        (
            await session.execute(select(func.count()).select_from(EvalRun).where(*filters))
        ).scalar_one()
    )
    runs = (
        (
            await session.execute(
                select(EvalRun)
                .where(*filters)
                .order_by(EvalRun.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return [_run_out(run) for run in runs], total


async def get_report(session: AsyncSession, *, tenant_id: str, run_id: str) -> EvalReportOut:
    run = await session.get(EvalRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise NotFound("评测记录不存在")
    results = (
        (
            await session.execute(
                select(EvalResult)
                .where(EvalResult.run_id == run_id)
                .order_by(EvalResult.result_id.asc())
            )
        )
        .scalars()
        .all()
    )
    metrics = run.metrics or [
        {"label": "Recall@10", "value": run.recall, "display": f"{run.recall * 100:.1f}%"},
        {"label": "答案准确率", "value": run.accuracy, "display": f"{run.accuracy * 100:.1f}%"},
        {
            "label": "引用准确率",
            "value": run.citation_accuracy,
            "display": f"{run.citation_accuracy * 100:.1f}%",
        },
    ]
    return EvalReportOut(
        run_id=run.run_id,
        created_at=fmt_dt(run.created_at),
        metrics=[EvalMetricOut(**item) for item in metrics],
        failures=[
            EvalFailureOut(question=item.question, reason=item.reason, tone="danger")
            for item in results
            if not item.passed
        ],
    )


async def _resolve_dataset(
    session: AsyncSession, tenant_id: str, label: str | None
) -> EvalDataset:
    datasets = (
        (
            await session.execute(
                select(EvalDataset)
                .where(EvalDataset.tenant_id == tenant_id)
                .order_by(EvalDataset.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not datasets:
        raise AppError("尚未配置评测数据集")
    if not label:
        return datasets[0]
    base = label.split("·", 1)[0].strip()
    for dataset in datasets:
        if dataset.name == label or dataset.name == base:
            return dataset
    return datasets[0]


async def _compute(session: AsyncSession, run: EvalRun) -> None:
    cases = (
        (
            await session.execute(
                select(EvalCase).where(EvalCase.dataset_id == run.dataset_id)
            )
        )
        .scalars()
        .all()
    )
    published = [
        doc
        async for doc in col("questions").find(
            {"tenant_id": run.tenant_id, "review_status": "published"},
            {"knowledge_point_ids": 1, "quality_score": 1, "stem": 1},
        )
    ]
    by_kp: dict[str, list[dict]] = {}
    for question in published:
        for kp_id in question.get("knowledge_point_ids", []) or []:
            by_kp.setdefault(kp_id, []).append(question)

    recall_hits = 0
    accuracy_total = 0.0
    citation_hits = 0
    results: list[EvalResult] = []
    for case in cases:
        matches: list[dict] = []
        for kp_id in case.expected_kp_ids or []:
            matches.extend(by_kp.get(kp_id, []))
        matched = bool(matches)
        score = (
            sum(float(item.get("quality_score") or 0) for item in matches) / len(matches)
            if matches
            else 0.0
        )
        has_evidence = False
        if case.expected_kp_ids:
            has_evidence = await get_chunk_store().has_evidence(
                run.tenant_id, list(case.expected_kp_ids)
            )
        if matched:
            recall_hits += 1
            accuracy_total += score
        if has_evidence:
            citation_hits += 1
        reason = ""
        passed = matched and score >= 0.6
        if not matched:
            reason = "未召回关联知识点对应的已发布题目"
        elif score < 0.6:
            reason = f"命中题目质量分偏低（{score:.2f}）"
        results.append(
            EvalResult(
                result_id=new_id("evr"),
                run_id=run.run_id,
                case_id=case.case_id,
                question=case.question,
                reason=reason,
                passed=passed,
                score=round(score, 4),
            )
        )

    total = len(cases) or 1
    recall = recall_hits / total
    accuracy = accuracy_total / recall_hits if recall_hits else 0.0
    citation = citation_hits / total
    run.recall = round(recall, 4)
    run.accuracy = round(accuracy, 4)
    run.citation_accuracy = round(citation, 4)
    run.status = (
        "passed"
        if recall >= settings.eval_recall_threshold and accuracy >= 0.8
        else "failed"
    )
    run.metrics = [
        {
            "label": "Recall@10",
            "value": round(recall, 4),
            "display": f"{recall * 100:.1f}%",
        },
        {
            "label": "答案准确率",
            "value": round(accuracy, 4),
            "display": f"{accuracy * 100:.1f}%",
        },
        {
            "label": "引用准确率",
            "value": round(citation, 4),
            "display": f"{citation * 100:.1f}%",
        },
        {
            "label": "题目覆盖率",
            "value": round(len(published) / max(len(cases), 1), 4),
            "display": f"{len(published)} 题",
        },
    ]
    run.finished_at = utcnow()
    session.add_all(results)


async def execute_run(run_id: str) -> None:
    async with SessionLocal() as session:
        run = await session.get(EvalRun, run_id)
        if run is None or run.status != "running":
            return
        try:
            await _compute(session, run)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            run = await session.get(EvalRun, run_id)
            if run is not None:
                run.status = "failed"
                run.metrics = [
                    {"label": "执行失败", "value": 0, "display": "0%", "detail": str(exc)}
                ]
                run.finished_at = utcnow()
                await session.commit()


async def create_run(
    session: AsyncSession, *, user: CurrentUser, payload: EvalRunIn
) -> EvalRunOut:
    dataset = await _resolve_dataset(session, user.tenant_id, payload.dataset)
    run = EvalRun(
        run_id=new_id("eval"),
        tenant_id=user.tenant_id,
        dataset_id=dataset.dataset_id,
        dataset_name=dataset.name,
        model=payload.model or settings.qwen_vl_model,
        status="running",
        created_by=user.id,
    )
    session.add(run)
    await session.flush()
    await _compute(session, run)
    audit.record(
        session,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_name=user.name,
        action="evaluation.run",
        resource_type="eval_run",
        resource_id=run.run_id,
        after={
            "dataset": dataset.name,
            "recall": run.recall,
            "accuracy": run.accuracy,
            "status": run.status,
        },
    )
    audit.emit(
        session,
        tenant_id=user.tenant_id,
        event_type="evaluation.completed",
        aggregate_type="eval_run",
        aggregate_id=run.run_id,
        payload={"status": run.status},
    )
    await session.commit()
    return _run_out(run)


def metric_display(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"
