"""Celery entry point for ingestion jobs.

The task itself delegates to the same pipeline used by inline mode, so the state
machine has a single implementation.
"""

from __future__ import annotations

import asyncio

from app.workers.celery_app import celery_app


@celery_app.task(name="ingestion.run", bind=True, max_retries=2)
def run_ingestion_job(self, job_id: str) -> None:
    from app.modules.ingestion.pipeline import run_ingestion_job as execute

    try:
        asyncio.run(execute(job_id))
    except Exception as exc:  # noqa: BLE001 - retry transient worker failures
        raise self.retry(exc=exc, countdown=15) from exc
