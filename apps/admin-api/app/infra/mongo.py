"""Motor client for knowledge assets.

When ``MONGO_URL`` is empty the process uses an in-memory mongomock database so
the admin API starts with no infrastructure. Production always sets MONGO_URL.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings


def _create_client() -> Any:
    if settings.mongo_url:
        return AsyncIOMotorClient(
            settings.mongo_url,
            serverSelectionTimeoutMS=settings.mongo_timeout_ms,
            tz_aware=True,
        )
    from mongomock_motor import AsyncMongoMockClient

    return AsyncMongoMockClient()


client = _create_client()
database = client[settings.mongo_db]


def col(name: str):
    return database[name]


async def ping() -> bool:
    try:
        await database.command("ping")
        return True
    except Exception:  # noqa: BLE001
        return False


async def init_indexes() -> None:
    """Indexes from 数据对象 §10. mongomock ignores most options but accepts the calls."""
    specs: list[tuple[str, list[tuple[str, int]], bool]] = [
        ("tenants", [("status", 1)], False),
        ("textbooks", [("tenant_id", 1), ("status", 1), ("updated_at", -1)], False),
        ("chapters", [("book_id", 1), ("no", 1)], False),
        ("licenses", [("tenant_id", 1), ("book_id", 1)], False),
        ("questions", [("tenant_id", 1), ("review_status", 1), ("updated_at", -1)], False),
        ("questions", [("tenant_id", 1), ("subject", 1), ("difficulty", 1)], False),
        ("question_variants", [("question_id", 1)], False),
        ("knowledge_points", [("tenant_id", 1), ("parent_id", 1), ("level", 1)], False),
        ("kp_relations", [("tenant_id", 1), ("source_id", 1), ("target_id", 1)], True),
        ("annotation_blocks", [("annotation_task_id", 1), ("block_index", 1)], False),
        ("api_keys", [("key_hash", 1)], True),
    ]
    for name, keys, unique in specs:
        try:
            await col(name).create_index(keys, unique=unique)
        except Exception:  # noqa: BLE001 - mongomock limitations should not break startup
            pass


async def close() -> None:
    client.close()
