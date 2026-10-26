"""Tests for the BGE-M3 vector representation and publish index rows."""

from __future__ import annotations

import pytest

from app.infra import mongo
from app.infra.chunk_store import get_chunk_store
from app.infra.vectors import sparse_to_document, sparse_to_field
from app.modules.publish.indexer import DENSE_DIM, _chunk_row, _question_row, build_version_index


def _dense(value: float = 0.01) -> list[float]:
    return [value] * DENSE_DIM


def test_sparse_weights_round_trip_between_mongo_and_milvus() -> None:
    stored = sparse_to_document({7: 0.1234567, 9: 0.0, 11: 1})
    assert stored == {"7": 0.123457, "11": 1.0}
    assert sparse_to_field(stored) == {7: 0.123457, 11: 1.0}
    assert sparse_to_field(None) == {0: 0.0}


def test_chunk_row_carries_filter_fields_and_vectors() -> None:
    row = _chunk_row(
        {
            "_id": "chk_1",
            "tenant_id": "t1",
            "book_id": "book_1",
            "subject": "数学",
            "stage": "high",
            "grade": "高一",
            "page": 2,
            "page_end": 3,
            "knowledge_point_ids": ["kp_1", "kp_2"],
            "content_type": "text",
            "dense_vector": _dense(0.5),
            "sparse_vector": {"12": 0.75},
        }
    )
    assert row["chunk_id"] == "chk_1"
    assert row["subject"] == "数学"
    assert row["page_start"] == 2
    assert row["page_end"] == 3
    assert row["kp_ids"] == ["kp_1", "kp_2"]
    assert row["dense_vector"][:2] == [0.5, 0.5]
    assert row["sparse_vector"] == {12: 0.75}


def test_question_row_uses_published_status_and_zero_solution() -> None:
    row = _question_row(
        {
            "_id": "q_1",
            "tenant_id": "t1",
            "subject": "数学",
            "stage": "high",
            "type": "single_choice",
            "difficulty": "easy",
            "review_status": "published",
            "knowledge_point_ids": ["kp_1"],
            "stem_vector": _dense(0.2),
            "solution_vector": [0.0] * DENSE_DIM,
            "sparse_vector": {"3": 0.4},
        }
    )
    assert row["question_id"] == "q_1"
    assert row["question_type"] == "single_choice"
    assert row["review_status"] == "published"
    assert row["kp_ids"] == ["kp_1"]
    assert row["sparse_vector"] == {3: 0.4}


@pytest.mark.usefixtures("client")
async def test_build_version_index_memory_mode_counts_and_backfills_metadata() -> None:
    await mongo.col("knowledge_points").insert_one(
        {"_id": "kp_x", "tenant_id": "t_vectors", "subject": "数学", "stage": "high"}
    )
    store = get_chunk_store()
    await store.upsert(
        [{"_id": "chk_x", "tenant_id": "t_vectors", "knowledge_point_ids": ["kp_x"]}]
    )
    await store.upsert([{"_id": "chk_y", "tenant_id": "other"}])
    await mongo.col("questions").insert_one(
        {"_id": "q_x", "tenant_id": "t_vectors", "review_status": "published"}
    )
    await mongo.col("questions").insert_one(
        {"_id": "q_a", "tenant_id": "t_vectors", "review_status": "approved"}
    )
    await mongo.col("questions").insert_one(
        {"_id": "q_y", "tenant_id": "t_vectors", "review_status": "reviewing"}
    )

    result = await build_version_index(tenant_id="t_vectors", version="20990101_000000")
    assert result.chunks == 1
    # ``approved`` ships in this release: commit promotes it after the alias switch.
    assert result.questions == 2
    assert result.embedded_chunks == 0
    assert result.embedded_questions == 0

    chunk = next(doc for doc in await store.all("t_vectors") if doc["_id"] == "chk_x")
    assert chunk["subject"] == "数学"
    assert chunk["stage"] == "high"
