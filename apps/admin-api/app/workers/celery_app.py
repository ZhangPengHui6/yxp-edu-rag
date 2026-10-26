"""Celery application. Only used when ``TASK_MODE=celery``."""

from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "yxp-admin",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.parse_tasks", "app.workers.eval_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=1800,
    task_soft_time_limit=1500,
    broker_connection_retry_on_startup=True,
)
