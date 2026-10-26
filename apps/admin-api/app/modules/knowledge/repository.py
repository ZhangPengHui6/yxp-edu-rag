"""Mongo access for knowledge points and relations; Milvus for evidence chunks."""

from __future__ import annotations

from datetime import UTC, datetime

from app.infra.chunk_store import get_chunk_store
from app.infra.mongo import col


async def list_nodes(tenant_id: str) -> list[dict]:
    cursor = col("knowledge_points").find({"tenant_id": tenant_id}).sort(
        [("level", 1), ("order", 1), ("name", 1)]
    )
    return [doc async for doc in cursor]


async def get_node(tenant_id: str, node_id: str) -> dict | None:
    return await col("knowledge_points").find_one({"_id": node_id, "tenant_id": tenant_id})


async def question_kp_projection(tenant_id: str) -> list[dict]:
    cursor = col("questions").find(
        {"tenant_id": tenant_id, "review_status": {"$ne": "deprecated"}},
        {"knowledge_point_ids": 1, "type": 1, "difficulty": 1},
    )
    return [doc async for doc in cursor]


async def relations_for(tenant_id: str, node_id: str) -> list[dict]:
    cursor = col("kp_relations").find(
        {
            "tenant_id": tenant_id,
            "$or": [{"source_id": node_id}, {"target_id": node_id}],
        }
    )
    return [doc async for doc in cursor]


async def names_by_ids(tenant_id: str, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    cursor = col("knowledge_points").find(
        {"tenant_id": tenant_id, "_id": {"$in": ids}}, {"name": 1}
    )
    return {doc["_id"]: doc.get("name", "") async for doc in cursor}


async def evidence_chunks(tenant_id: str, node_id: str, limit: int = 10) -> list[dict]:
    return await get_chunk_store().evidence(tenant_id, node_id, limit)


async def merge_into(tenant_id: str, source: dict, target: dict) -> int:
    source_id = source["_id"]
    target_id = target["_id"]
    affected = [
        doc["_id"]
        async for doc in col("questions").find(
            {"tenant_id": tenant_id, "knowledge_point_ids": source_id}, {"_id": 1}
        )
    ]
    moved = len(affected)
    await col("questions").update_many(
        {"_id": {"$in": affected}},
        {"$pull": {"knowledge_point_ids": source_id}},
    )
    await col("questions").update_many(
        {"_id": {"$in": affected}},
        {"$addToSet": {"knowledge_point_ids": target_id}},
    )
    # The denormalised name arrays are used for display and search.
    affected_by_name = [
        doc["_id"]
        async for doc in col("questions").find(
            {"tenant_id": tenant_id, "knowledge_points": source.get("name")}, {"_id": 1}
        )
    ]
    await col("questions").update_many(
        {"_id": {"$in": affected_by_name}},
        {"$pull": {"knowledge_points": source.get("name")}},
    )
    await col("questions").update_many(
        {"_id": {"$in": affected_by_name}},
        {"$addToSet": {"knowledge_points": target.get("name")}},
    )
    await col("kp_relations").update_many(
        {"tenant_id": tenant_id, "source_id": source_id},
        {"$set": {"source_id": target_id}},
    )
    await col("kp_relations").update_many(
        {"tenant_id": tenant_id, "target_id": source_id},
        {"$set": {"target_id": target_id}},
    )
    await col("kp_relations").delete_many(
        {"tenant_id": tenant_id, "source_id": target_id, "target_id": target_id}
    )
    now = datetime.now(UTC)
    await col("knowledge_points").update_one(
        {"_id": target_id},
        {
            "$inc": {"question_count": moved},
            "$set": {"updated_at": now},
        },
    )
    await col("knowledge_points").update_one(
        {"_id": source_id},
        {
            "$set": {
                "status": "deprecated",
                "question_count": 0,
                "deprecated_at": now,
                "updated_at": now,
                "merged_into": target_id,
            }
        },
    )
    return moved
