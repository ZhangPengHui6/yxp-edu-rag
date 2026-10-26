"""Mongo access for the question bank."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.infra.mongo import col


async def list_questions(
    tenant_id: str,
    *,
    page: int,
    page_size: int,
    status: str | None,
    type_value: str | None,
    difficulty: str | None,
    keyword: str | None,
) -> tuple[list[dict], int]:
    query: dict[str, Any] = {"tenant_id": tenant_id}
    if status and status != "all":
        if status == "reviewing":
            # approved-but-not-published questions are exposed as reviewing.
            query["review_status"] = {"$in": ["reviewing", "approved"]}
        else:
            query["review_status"] = status
    if type_value and type_value != "all":
        query["type"] = type_value
    if difficulty and difficulty != "all":
        query["difficulty"] = difficulty
    if keyword:
        query["stem"] = {"$regex": re.escape(keyword), "$options": "i"}
    total = await col("questions").count_documents(query)
    cursor = (
        col("questions")
        .find(query)
        .sort("updated_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [doc async for doc in cursor], total


async def get_question(tenant_id: str, question_id: str) -> dict | None:
    return await col("questions").find_one({"_id": question_id, "tenant_id": tenant_id})


async def get_variants(question_id: str) -> list[dict]:
    return [doc async for doc in col("question_variants").find({"question_id": question_id})]


async def update_question(question_id: str, values: dict[str, Any]) -> dict | None:
    values["updated_at"] = datetime.now(UTC)
    values["version"] = 1
    await col("questions").update_one({"_id": question_id}, {"$set": values, "$inc": {"version": 1}})
    return await col("questions").find_one({"_id": question_id})


async def knowledge_lookup(tenant_id: str, names: list[str]) -> dict[str, str]:
    if not names:
        return {}
    cursor = col("knowledge_points").find(
        {"tenant_id": tenant_id, "name": {"$in": names}},
        {"_id": 1, "name": 1},
    )
    return {doc["name"]: doc["_id"] async for doc in cursor}


async def knowledge_names(tenant_id: str, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    cursor = col("knowledge_points").find(
        {"tenant_id": tenant_id, "_id": {"$in": ids}},
        {"_id": 1, "name": 1},
    )
    return {doc["_id"]: doc["name"] async for doc in cursor}


async def all_questions(tenant_id: str, limit: int = 500) -> list[dict]:
    return [doc async for doc in col("questions").find({"tenant_id": tenant_id}).limit(limit)]


async def set_review_status(asset_id: str, status: str) -> None:
    await col("questions").update_one(
        {"_id": asset_id},
        {"$set": {"review_status": status, "updated_at": datetime.now(UTC)}},
    )
