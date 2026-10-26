"""Copy legacy Mongo ``chunks`` into the Milvus chunk store.

Before the Milvus-only refactor, ingestion wrote chunk documents into the
MongoDB ``chunks`` collection. New ingestion writes them straight to Milvus
(``MILVUS_CHUNKS_COLLECTION``, default ``chunks_live``). This one-off tool moves
the existing rows so no re-OCR is needed.

Usage (from ``apps/admin-api``)::

    uv run --env-file .env.remote python tools/migrate_chunks_to_milvus.py
    uv run --env-file .env.remote python tools/migrate_chunks_to_milvus.py --purge

Without ``--purge`` Mongo keeps its rows; with it, the rows are first backed up
to ``data/legacy_chunks_<timestamp>.jsonl`` and deleted only after the Milvus
counts match.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.infra import mongo
from app.infra.chunk_store import get_chunk_store


def _clean(doc: dict[str, Any]) -> dict[str, Any]:
    """Keep the chunk fields plus vectors; JSON-safe copy for the backup."""
    return json.loads(json.dumps(doc, ensure_ascii=False, default=str))


async def _load() -> list[dict[str, Any]]:
    rows = await mongo.col("chunks").find({}).to_list(length=None)
    docs: list[dict[str, Any]] = []
    for row in rows:
        doc = dict(row)
        doc["_id"] = str(doc["_id"])
        if not doc.get("dense_vector"):
            print(f"skip {doc['_id']}: no dense_vector")
            continue
        docs.append(doc)
    return docs


async def _counts_by_job(docs: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for doc in docs:
        key = f"{doc.get('tenant_id')}/{doc.get('job_id')}"
        counts[key] = counts.get(key, 0) + 1
    return counts


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--purge", action="store_true", help="delete Mongo rows after verifying")
    args = parser.parse_args()

    if not settings.milvus_uri:
        raise SystemExit("MILVUS_URI is empty; refusing to migrate (would use the memory store)")

    docs = await _load()
    print(f"mongo chunks to migrate: {len(docs)}")
    expected = await _counts_by_job(docs)
    for key, count in sorted(expected.items()):
        print(f"  {key}: {count}")

    store = get_chunk_store()
    written = await store.upsert(docs)
    print(f"milvus upserted: {written}")

    failures: list[str] = []
    for key, count in sorted(expected.items()):
        tenant_id, job_id = key.split("/", 1)
        actual = await store.count_by_job(tenant_id, job_id)
        status = "ok" if actual == count else "MISMATCH"
        if actual != count:
            failures.append(f"{key}: mongo={count} milvus={actual}")
        print(f"  verify {key}: mongo={count} milvus={actual} {status}")

    if failures:
        raise SystemExit("migration incomplete, Mongo rows kept:\n" + "\n".join(failures))

    if not args.purge:
        print("migration verified; Mongo rows kept (pass --purge to remove them)")
        await mongo.close()
        return

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup = Path("data") / f"legacy_chunks_{stamp}.jsonl"
    backup.parent.mkdir(parents=True, exist_ok=True)
    with backup.open("w", encoding="utf-8") as handle:
        for doc in docs:
            handle.write(json.dumps(_clean(doc), ensure_ascii=False) + "\n")
    print(f"backup written: {backup}")

    deleted = await mongo.col("chunks").delete_many({"_id": {"$in": [doc["_id"] for doc in docs]}})
    print(f"deleted mongo chunks: {deleted.deleted_count}")
    print(f"milvus collection: {settings.milvus_chunks_collection}")
    await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
