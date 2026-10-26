"""Chunk (Milvus) and metadata (Mongo) persistence used by the struct/quality nodes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.infra import mongo
from app.infra.chunk_store import get_chunk_store
from app.modules.ingestion.evidence import Evidence


async def load_existing_knowledge_points(
    tenant_id: str, subject: str, stage: str
) -> dict[tuple[str | None, str], str]:
    known: dict[tuple[str | None, str], str] = {}
    cursor = mongo.col("knowledge_points").find(
        {"tenant_id": tenant_id, "subject": subject, "stage": stage},
        {"_id": 1, "parent_id": 1, "name": 1},
    )
    async for doc in cursor:
        known[(doc.get("parent_id"), doc.get("name", ""))] = doc["_id"]
    return known


async def clear_job_assets(tenant_id: str, job_id: str) -> None:
    """Drop a previous attempt's chunks/questions so retries do not leave stale rows."""
    await get_chunk_store().delete_job(tenant_id, job_id)
    await mongo.col("questions").delete_many({"tenant_id": tenant_id, "job_id": job_id})


async def write_knowledge_points(nodes: list[dict[str, Any]], now: datetime) -> None:
    if not nodes:
        return
    collection = mongo.col("knowledge_points")
    for node in nodes:
        doc = dict(node)
        doc.pop("paragraph_count", None)
        doc.pop("question_count", None)
        doc.pop("_id", None)
        await collection.update_one(
            {"_id": node["_id"]},
            {"$setOnInsert": {**doc, "created_at": now}, "$set": {"updated_at": now}},
            upsert=True,
        )


async def write_chunks(docs: list[dict[str, Any]]) -> None:
    """Write chunks and their vectors to Milvus (the only chunk store)."""
    if not docs:
        return
    await get_chunk_store().upsert(docs)


async def write_questions(docs: list[dict[str, Any]]) -> None:
    if not docs:
        return
    collection = mongo.col("questions")
    for doc in docs:
        await collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)


async def apply_question_evidence(
    docs: list[dict[str, Any]], evidence: dict[int, Evidence]
) -> int:
    """Persist reranker output on the question documents."""
    applied = 0
    for index, item in evidence.items():
        if index < 0 or index >= len(docs):
            continue
        doc = docs[index]
        doc["evidence_chunk_ids"] = item.chunk_ids
        doc["evidence_score"] = item.score
        await mongo.col("questions").update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "evidence_chunk_ids": item.chunk_ids,
                    "evidence_score": item.score,
                }
            },
        )
        applied += 1
    return applied


async def refresh_knowledge_counts(tenant_id: str, node_ids: list[str]) -> None:
    if not node_ids:
        return
    counts = await get_chunk_store().kp_counts(tenant_id, node_ids)
    for node_id, count in counts.items():
        await mongo.col("knowledge_points").update_one(
            {"_id": node_id, "tenant_id": tenant_id},
            {"$set": {"paragraph_count": int(count)}},
        )
    pipeline = [
        {"$match": {"tenant_id": tenant_id, "knowledge_point_ids": {"$in": node_ids}}},
        {"$unwind": "$knowledge_point_ids"},
        {"$group": {"_id": "$knowledge_point_ids", "count": {"$sum": 1}}},
    ]
    async for row in mongo.col("questions").aggregate(pipeline):
        await mongo.col("knowledge_points").update_one(
            {"_id": row["_id"], "tenant_id": tenant_id},
            {"$set": {"question_count": int(row["count"])}},
        )
