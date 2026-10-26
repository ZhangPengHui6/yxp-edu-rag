"""Mongo access for OCR/annotation blocks (``annotation_blocks``).

Chunks live in Milvus, so low-confidence OCR blocks have their own Mongo
collection instead of sharing the former ``chunks`` collection.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.infra.mongo import col


async def blocks_for(task_id: str) -> list[dict]:
    cursor = col("annotation_blocks").find({"annotation_task_id": task_id}).sort("block_index", 1)
    return [doc async for doc in cursor]


async def block_counts(task_id: str) -> tuple[int, int]:
    total = await col("annotation_blocks").count_documents({"annotation_task_id": task_id})
    pending = await col("annotation_blocks").count_documents(
        {"annotation_task_id": task_id, "status": {"$ne": "resolved"}}
    )
    return total, pending


async def get_block(task_id: str, block_id: str) -> dict | None:
    return await col("annotation_blocks").find_one(
        {"_id": block_id, "annotation_task_id": task_id}
    )


async def update_block(block_id: str, *, status: str, content: str | None) -> None:
    values: dict = {"status": status, "updated_at": datetime.now(UTC)}
    if content is not None:
        values["content"] = content
    await col("annotation_blocks").update_one({"_id": block_id}, {"$set": values})
