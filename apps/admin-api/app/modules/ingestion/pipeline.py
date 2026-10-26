"""Ingestion job entry point, orchestrated by a LangGraph state machine.

The five pipeline steps live in :mod:`app.modules.ingestion.graph`:

``upload → ocr → split → struct → quality``

This module only owns job-level concerns — loading the job, choosing the mode,
invoking the compiled graph and persisting success/failure on
``ingestion_job``. Node responsibilities and step bookkeeping stay in
``graph/nodes`` and ``graph/base.py``.

Two modes are supported:

* ``OCR_PROVIDER=auto`` (or ``mineru`` / ``qwen_vl``) runs the real pipeline:
  read the uploaded object, call the OCR provider (MinerU, with Qwen-VL as a
  fallback), split the result into chunks, call Qwen to extract questions and
  knowledge points (deterministic rules fill gaps), then rerank question
  evidence before writing everything to MongoDB.
* any other value (``placeholder`` in tests) keeps the simulated state machine
  so the admin UI, retry and cancel semantics can be exercised without external
  services.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.infra.pg import SessionLocal
from app.models import IngestionJob, IngestionJobStep
from app.modules.ingestion.graph import deps
from app.modules.ingestion.graph.builder import run_ingestion_graph
from app.modules.ingestion.graph.state import IngestionRuntime, IngestionState
from app.shared.time import utcnow

log = get_logger("pipeline")

REAL_PROVIDERS = {"auto", "mineru", "qwen_vl"}


def use_real_pipeline() -> bool:
    return settings.ocr_provider.strip().lower() in REAL_PROVIDERS


async def _load_steps(session, job_id: str) -> list[IngestionJobStep]:
    return list(
        (
            await session.execute(
                select(IngestionJobStep)
                .where(IngestionJobStep.job_id == job_id)
                .order_by(IngestionJobStep.position.asc())
            )
        )
        .scalars()
        .all()
    )


async def run_ingestion_job(job_id: str) -> None:
    async with SessionLocal() as session:
        job = await session.get(IngestionJob, job_id)
        if job is None or job.status not in {"pending", "running"}:
            return

        steps = await _load_steps(session, job_id)
        if not steps:
            job.status = "failed"
            job.error_message = "任务缺少步骤定义"
            job.finished_at = utcnow()
            await session.commit()
            return

        job.status = "running"
        job.started_at = job.started_at or utcnow()
        job.progress = 0
        await session.commit()

        try:
            mode = "real" if use_real_pipeline() else "simulated"
            runtime = IngestionRuntime(
                session=session,
                job=job,
                steps=steps,
                provider=deps.get_ocr_provider() if mode == "real" else None,
            )
            result: dict[str, Any] = await run_ingestion_graph(
                IngestionState(job_id=job_id, mode=mode, runtime=runtime)
            )

            await session.refresh(job)
            if job.status == "canceled" or result.get("canceled"):
                return
            job.status = "succeeded"
            job.progress = 100
            job.finished_at = utcnow()
            stats = result.get("stats") or (
                {"pipeline": "poc-simulated"} if mode == "simulated" else {}
            )
            merged = dict(job.stats or {})
            merged.update(stats)
            job.stats = merged
            await session.commit()
            log.info("ingestion_job_succeeded", job_id=job_id, stats=stats)
        except Exception as exc:  # noqa: BLE001 - persist failure for retry
            await session.rollback()
            job = await session.get(IngestionJob, job_id)
            if job is None:
                return
            job.status = "failed"
            if isinstance(exc, ProviderUnavailable):
                job.error_code = "PROVIDER_UNAVAILABLE"
            elif isinstance(exc, FileNotFoundError):
                job.error_code = "SOURCE_NOT_FOUND"
            else:
                job.error_code = "PIPELINE_ERROR"
            job.error_message = str(exc)[:1000]
            job.finished_at = utcnow()
            steps = await _load_steps(session, job_id)
            for step in steps:
                if step.status == "running":
                    step.status = "failed"
                    step.detail = str(exc)[:500]
                    step.finished_at = utcnow()
            await session.commit()
            log.exception("ingestion_job_failed", job_id=job_id)
