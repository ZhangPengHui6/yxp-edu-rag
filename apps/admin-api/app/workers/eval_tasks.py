"""Celery entry point for offline evaluation runs."""

from __future__ import annotations

import asyncio

from app.workers.celery_app import celery_app


@celery_app.task(name="evaluation.run")
def run_evaluation(run_id: str) -> None:
    from app.modules.evaluation.service import execute_run

    asyncio.run(execute_run(run_id))
