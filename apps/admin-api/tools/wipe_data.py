"""Wipe ingested knowledge assets and pipeline history for a clean re-upload.

Removes every business row written by ingestion / annotation / review / publish
/ evaluation, plus the Milvus chunk collections and their aliases. IAM data
(``sys_user`` / ``sys_role`` / ``sys_permission`` + join tables) and the Mongo
``tenants`` / ``api_keys`` collections are **kept** so the admin console can
still log in.

Usage (from ``apps/admin-api``, backend stopped)::

    uv run --env-file .env.remote python tools/wipe_data.py --dry-run
    uv run --env-file .env.remote python tools/wipe_data.py --yes
    uv run --env-file .env.remote python tools/wipe_data.py --yes --keep-audit

Milvus collections whose name starts with ``chunks_`` or ``questions_`` are
dropped, which includes ``chunks_live`` (recreated on the next ingestion) and
every versioned ``chunks_<version>`` / ``questions_<version>``. The student-side
collections (``kb_chunks``, ``edu_kb_chunks``, ``kb_item_names``) are untouched.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from sqlalchemy import delete, func, select

from app.core.config import settings
from app.infra import mongo
from app.infra.pg import SessionLocal, dispose_engine
from app.models import (
    AnnotationTask,
    AuditLog,
    EvalCase,
    EvalDataset,
    EvalResult,
    EvalRun,
    IngestionJob,
    IngestionJobStep,
    OutboxEvent,
    PublishItem,
    PublishOrder,
    ReviewTask,
)

# Delete order matters where a foreign-key-ish relationship exists; these tables
# have no FKs enforced but keeping child-first order keeps the intent readable.
PG_TABLES = [
    IngestionJobStep,
    IngestionJob,
    AnnotationTask,
    ReviewTask,
    PublishItem,
    PublishOrder,
    EvalResult,
    EvalRun,
    EvalCase,
    EvalDataset,
    OutboxEvent,
]

MONGO_COLLECTIONS = [
    "knowledge_points",
    "kp_relations",
    "questions",
    "question_variants",
    "chapters",
    "textbooks",
    "licenses",
    "annotation_blocks",
    "chunks",  # legacy Mongo-era rows; new chunks live in Milvus
]

MILVUS_PREFIXES = ("chunks_", "questions_")


async def _pg_counts(tables: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with SessionLocal() as session:
        for model in tables:
            counts[model.__tablename__] = int(
                (await session.execute(select(func.count()).select_from(model))).scalar_one()
            )
    return counts


async def _mongo_counts(names: list[str]) -> dict[str, int]:
    return {name: await mongo.col(name).count_documents({}) for name in names}


def _milvus_targets() -> tuple[list[str], list[str]]:
    from pymilvus import MilvusClient

    client = MilvusClient(uri=settings.milvus_uri)
    try:
        names = client.list_collections()
        collections = [name for name in names if name.startswith(MILVUS_PREFIXES)]
        aliases = [
            alias
            for alias in (settings.chunks_alias, settings.questions_alias)
            if _alias_exists(client, alias)
        ]
        return collections, aliases
    finally:
        client.close()


def _alias_exists(client, alias: str) -> bool:
    try:
        client.describe_alias(alias)
        return True
    except Exception:  # noqa: BLE001 - pymilvus raises on a missing alias
        return False


def _milvus_wipe(collections: list[str], aliases: list[str]) -> None:
    from pymilvus import MilvusClient

    client = MilvusClient(uri=settings.milvus_uri)
    try:
        for alias in aliases:
            client.drop_alias(alias)
            print(f"  milvus alias dropped: {alias}")
        for name in collections:
            client.drop_collection(name)
            print(f"  milvus collection dropped: {name}")
    finally:
        client.close()


async def _wipe(keep_audit: bool) -> None:
    pg_tables = list(PG_TABLES)
    if not keep_audit:
        pg_tables.append(AuditLog)

    print("before:")
    for name, count in (await _pg_counts(pg_tables)).items():
        print(f"  pg {name}: {count}")
    for name, count in (await _mongo_counts(MONGO_COLLECTIONS)).items():
        print(f"  mongo {name}: {count}")
    collections, aliases = _milvus_targets()
    for name in collections:
        print(f"  milvus collection {name}")
    for alias in aliases:
        print(f"  milvus alias {alias}")

    async with SessionLocal() as session:
        for model in pg_tables:
            result = await session.execute(delete(model))
            print(f"  deleted pg {model.__tablename__}: {result.rowcount}")
        await session.commit()

    for name in MONGO_COLLECTIONS:
        result = await mongo.col(name).delete_many({})
        print(f"  deleted mongo {name}: {result.deleted_count}")

    _milvus_wipe(collections, aliases)

    print("after:")
    for name, count in (await _pg_counts(pg_tables)).items():
        print(f"  pg {name}: {count}")
    for name, count in (await _mongo_counts(MONGO_COLLECTIONS)).items():
        print(f"  mongo {name}: {count}")
    remaining, remaining_aliases = _milvus_targets()
    print(f"  milvus collections left: {remaining or 'none'}")
    print(f"  milvus aliases left: {remaining_aliases or 'none'}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="wipe ingested data, keep IAM")
    parser.add_argument("--yes", action="store_true", help="actually delete")
    parser.add_argument("--dry-run", action="store_true", help="print what would be deleted")
    parser.add_argument("--keep-audit", action="store_true", help="keep audit_log rows")
    args = parser.parse_args()

    if not args.yes and not args.dry_run:
        parser.error("pass --yes to delete, or --dry-run to preview")
    if not settings.milvus_uri:
        print("MILVUS_URI is empty; the chunk store is in-process memory and holds no data.")
    if args.dry_run:
        print("dry run; nothing deleted")
        pg_tables = list(PG_TABLES) + ([] if args.keep_audit else [AuditLog])
        for name, count in (await _pg_counts(pg_tables)).items():
            print(f"  pg {name}: {count}")
        for name, count in (await _mongo_counts(MONGO_COLLECTIONS)).items():
            print(f"  mongo {name}: {count}")
        if settings.milvus_uri:
            collections, aliases = _milvus_targets()
            print(f"  milvus drop: {collections} aliases {aliases}")
        await mongo.close()
        await dispose_engine()
        return

    await _wipe(args.keep_audit)
    await mongo.close()
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
