"""Background task dispatch: Celery in production, inline task in POC."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Coroutine
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("queue")

_inline_tasks: set[asyncio.Task] = set()


def _spawn(coro: Coroutine[Any, Any, Any]) -> None:
    task = asyncio.create_task(coro)
    _inline_tasks.add(task)
    task.add_done_callback(_inline_tasks.discard)


def dispatch_ingestion(job_id: str) -> None:
    if settings.task_mode == "celery":
        from app.workers.parse_tasks import run_ingestion_job

        run_ingestion_job.delay(job_id)
        return
    from app.modules.ingestion.pipeline import run_ingestion_job

    _spawn(run_ingestion_job(job_id))


def dispatch_ingestion_with_context(job_id: str, ctx: contextvars.Context) -> None:
    """Copy the request context into the background task (request id for logs)."""
    if settings.task_mode == "celery":
        dispatch_ingestion(job_id)
        return
    from app.modules.ingestion.pipeline import run_ingestion_job

    task = asyncio.create_task(run_ingestion_job(job_id), context=ctx)
    _inline_tasks.add(task)
    task.add_done_callback(_inline_tasks.discard)


def dispatch_evaluation(run_id: str) -> None:
    if settings.task_mode == "celery":
        from app.workers.eval_tasks import run_evaluation

        run_evaluation.delay(run_id)
        return
    from app.modules.evaluation.service import execute_run

    _spawn(execute_run(run_id))


async def drain_inline_tasks() -> None:
    """Used by tests and graceful shutdown."""
    while _inline_tasks:
        pending = list(_inline_tasks)
        await asyncio.gather(*pending, return_exceptions=True)
