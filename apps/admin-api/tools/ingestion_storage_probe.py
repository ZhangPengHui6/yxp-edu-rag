"""Diagnostic: current chunk/question/knowledge storage state.

Counts Mongo assets, the Milvus chunk store (`chunks_live`), Milvus version
collections and aliases, and PostgreSQL ingestion job statuses.
"""

from __future__ import annotations

import asyncio
from collections import Counter

from sqlalchemy import select

from app.core.config import settings
from app.infra import mongo
from app.infra.chunk_store import get_chunk_store
from app.infra.pg import SessionLocal
from app.models import IngestionJob


async def main() -> None:
    for name in ("textbooks", "chapters", "questions", "knowledge_points", "annotation_blocks", "chunks"):
        print(f"mongo {name}: {await mongo.col(name).count_documents({})}")

    store = get_chunk_store()
    print(f"chunk store: {getattr(store, 'collection', 'memory')}")
    tenant_ids = await mongo.col("tenants").distinct("_id")
    for tenant_id in tenant_ids:
        print(f"  tenant {tenant_id}: chunks={await store.count(tenant_id)}")

    async with SessionLocal() as session:
        rows = (await session.execute(select(IngestionJob.status))).scalars().all()
    print("pg ingestion_job:", dict(Counter(rows)))

    if not settings.milvus_uri:
        await mongo.close()
        return
    from pymilvus import MilvusClient

    client = MilvusClient(uri=settings.milvus_uri)
    for name in sorted(client.list_collections()):
        try:
            stats = client.get_collection_stats(name)
            rows = stats.get("row_count")
        except Exception as exc:  # noqa: BLE001
            rows = f"err:{type(exc).__name__}"
        print(f"milvus collection {name}: rows={rows}")
    for alias in (settings.chunks_alias, settings.questions_alias):
        try:
            info = client.describe_alias(alias)
            print(f"alias {alias}: {info}")
        except Exception:  # noqa: BLE001
            print(f"alias {alias}: none")
    client.close()
    await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
