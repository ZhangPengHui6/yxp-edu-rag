"""Mongo access for textbooks, chapters and licences."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.infra.mongo import col


def _tenant(tenant_id: str) -> dict[str, Any]:
    return {"tenant_id": tenant_id}


async def list_books(
    tenant_id: str,
    *,
    page: int,
    page_size: int,
    status: str | None,
    subject: str | None,
    keyword: str | None,
) -> tuple[list[dict], int]:
    query: dict[str, Any] = _tenant(tenant_id)
    if status and status != "all":
        query["status"] = status
    if subject and subject != "all":
        query["subject"] = subject
    if keyword:
        query["title"] = {"$regex": re.escape(keyword), "$options": "i"}
    total = await col("textbooks").count_documents(query)
    cursor = (
        col("textbooks")
        .find(query)
        .sort("updated_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [doc async for doc in cursor], total


async def chapters_for(book_ids: list[str]) -> dict[str, list[dict]]:
    if not book_ids:
        return {}
    cursor = col("chapters").find({"book_id": {"$in": book_ids}}).sort("no", 1)
    grouped: dict[str, list[dict]] = {book_id: [] for book_id in book_ids}
    async for doc in cursor:
        grouped.setdefault(doc["book_id"], []).append(doc)
    return grouped


async def licenses_for(book_ids: list[str]) -> dict[str, dict]:
    if not book_ids:
        return {}
    cursor = col("licenses").find({"book_id": {"$in": book_ids}})
    return {doc["book_id"]: doc async for doc in cursor}


async def get_book(tenant_id: str, book_id: str) -> dict | None:
    return await col("textbooks").find_one({"_id": book_id, **_tenant(tenant_id)})


async def get_chapters(book_id: str) -> list[dict]:
    return [doc async for doc in col("chapters").find({"book_id": book_id}).sort("no", 1)]


async def get_license(book_id: str) -> dict | None:
    return await col("licenses").find_one({"book_id": book_id})


async def upsert_license(
    tenant_id: str, book_id: str, *, holder: str, scope: str, expires_at
) -> dict:
    existing = await get_license(book_id)
    if existing:
        await col("licenses").update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "holder": holder,
                    "scope": scope,
                    "end_at": expires_at,
                    "updated_at": datetime.now(UTC),
                }
            },
        )
        return await get_license(book_id) or existing
    doc = {
        "_id": f"lic_{book_id.split('_', 1)[-1]}",
        "tenant_id": tenant_id,
        "book_id": book_id,
        "holder": holder,
        "scope": scope,
        "start_at": datetime.now(UTC),
        "end_at": expires_at,
        "updated_at": datetime.now(UTC),
    }
    await col("licenses").insert_one(doc)
    return doc
