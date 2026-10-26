from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.core.envelope import ok
from app.infra.milvus import get_vector_index
from app.infra.minio import get_storage
from app.infra.mongo import ping as mongo_ping
from app.infra.pg import ping as pg_ping

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    db_ok = await pg_ping()
    mongo_ok = await mongo_ping()
    vector = await get_vector_index().health()
    storage = await get_storage().health()
    status = "ok" if db_ok and mongo_ok and vector.get("ok") and storage.get("ok") else "degraded"
    return ok(
        {
            "status": status,
            "env": settings.app_env,
            "db": {"backend": "postgresql" if not settings.is_sqlite else "sqlite", "ok": db_ok},
            "mongo": {
                "backend": "mongodb" if settings.mongo_url else "mongomock",
                "ok": mongo_ok,
            },
            "vector": vector,
            "storage": storage,
            "taskMode": settings.task_mode,
        }
    )
