"""Remove demo/seed business rows, keeping IAM data.

`app/seed/demo.py` writes deterministic IDs (``tb_math_high_1``, ``job_math``,
``q_math_001`` ...) the first time an empty store starts. On the production
database those rows look like real business data in the admin UI, so this
module deletes exactly the seeded IDs plus the two local pipeline test jobs.

Users, roles, permissions and tenants are intentionally kept: they are what the
admin console authenticates against.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import delete, select

from app.core.logging import get_logger
from app.infra import mongo
from app.infra.chunk_store import get_chunk_store
from app.infra.pg import SessionLocal
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
    PublishOrder,
    ReviewTask,
)
from app.seed.demo import (
    ANNOTATION_BLOCKS,
    CHAPTERS,
    EVAL_RUNS,
    EVIDENCE,
    JOBS,
    KNOWLEDGE_NODES,
    LICENSES,
    PUBLISH_ORDERS,
    QUESTIONS,
    REVIEWS,
    TEXTBOOKS,
    VARIANTS,
)

log = get_logger("seed.purge")

# Jobs created while verifying the pipeline locally; they are not seed data but
# are just as fake as it.
TEST_JOB_NAMES = ("local-ingestion-test.pdf", "demo-math.pdf")

EVAL_DATASET_IDS = ("eval_ds_math", "eval_ds_physics")


def _ids() -> dict[str, list[str]]:
    books = [book["_id"] for book in TEXTBOOKS]
    return {
        "jobs": [job["id"] for job in JOBS],
        "publish": [order["id"] for order in PUBLISH_ORDERS],
        "reviews": [review["id"] for review in REVIEWS],
        "eval_runs": [run["id"] for run in EVAL_RUNS],
        "books": books,
        "chapters": [
            f"ch_{book_id}_{index}"
            for book_id in books
            for index in range(1, len(CHAPTERS[book_id]) + 1)
        ],
        "licenses": [license_id for license_id, *_ in LICENSES],
        "knowledge_points": [node["_id"] for node in KNOWLEDGE_NODES],
        "questions": [question["_id"] for question in QUESTIONS],
        "variants": [variant_id for variant_id, *_ in VARIANTS],
        "chunks": [chunk_id for chunk_id, *_ in EVIDENCE],
        "annotation_blocks": [block_id for block_id, *_ in ANNOTATION_BLOCKS],
    }


async def _test_job_ids(session) -> list[str]:
    return list(
        (
            await session.execute(
                select(IngestionJob.job_id).where(
                    IngestionJob.source_name.in_(TEST_JOB_NAMES)
                )
            )
        )
        .scalars()
        .all()
    )


async def _purge_pg(ids: dict[str, list[str]], job_ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with SessionLocal() as session:
        async def run(stmt) -> None:
            result = await session.execute(stmt)
            rowcount = int(getattr(result, "rowcount", 0) or 0)
            counts[stmt.table.name] = counts.get(stmt.table.name, 0) + rowcount

        await run(delete(IngestionJobStep).where(IngestionJobStep.job_id.in_(job_ids)))
        await run(delete(IngestionJob).where(IngestionJob.job_id.in_(job_ids)))
        await run(
            delete(PublishOrder).where(PublishOrder.publish_id.in_(ids["publish"]))
        )
        await run(delete(ReviewTask).where(ReviewTask.task_id.in_(ids["reviews"])))
        await run(
            delete(AnnotationTask).where(AnnotationTask.book_id.in_(ids["books"]))
        )
        await run(delete(EvalResult).where(EvalResult.run_id.in_(ids["eval_runs"])))
        await run(delete(EvalRun).where(EvalRun.run_id.in_(ids["eval_runs"])))
        await run(
            delete(EvalCase).where(EvalCase.dataset_id.in_(EVAL_DATASET_IDS))
        )
        await run(
            delete(EvalDataset).where(EvalDataset.dataset_id.in_(EVAL_DATASET_IDS))
        )
        await run(delete(AuditLog).where(AuditLog.request_id.like("req_audit_%")))
        await run(
            delete(OutboxEvent).where(
                OutboxEvent.aggregate_id.in_(
                    [*ids["publish"], *ids["knowledge_points"]]
                )
            )
        )
        await session.commit()

    return counts


async def _purge_mongo(ids: dict[str, list[str]], job_ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}

    async def drop(collection: str, query: dict[str, Any]) -> None:
        result = await mongo.col(collection).delete_many(query)
        counts[collection] = counts.get(collection, 0) + int(result.deleted_count)

    await drop("chapters", {"_id": {"$in": ids["chapters"]}})
    await drop("textbooks", {"_id": {"$in": ids["books"]}})
    await drop("licenses", {"_id": {"$in": ids["licenses"]}})
    await drop(
        "kp_relations",
        {
            "$or": [
                {"source_id": {"$in": ids["knowledge_points"]}},
                {"target_id": {"$in": ids["knowledge_points"]}},
            ]
        },
    )
    await drop("knowledge_points", {"_id": {"$in": ids["knowledge_points"]}})
    await drop("question_variants", {"_id": {"$in": ids["variants"]}})
    await drop("questions", {"_id": {"$in": ids["questions"]}})
    await drop("annotation_blocks", {"_id": {"$in": ids["annotation_blocks"]}})

    # Legacy rows from the Mongo chunk era are still removed when present; new
    # chunks live in Milvus and are deleted below.
    await drop("chunks", {"_id": {"$in": ids["chunks"]}})

    # Assets written by the local test jobs, if any.
    await drop("chunks", {"job_id": {"$in": job_ids}})
    await drop("questions", {"job_id": {"$in": job_ids}})

    store = get_chunk_store()
    counts["chunks"] = counts.get("chunks", 0) + await store.delete_ids(ids["chunks"])
    counts["chunks"] = counts.get("chunks", 0) + await store.delete_jobs(job_ids)
    return counts


async def purge_demo_data() -> dict[str, Any]:
    ids = _ids()
    async with SessionLocal() as session:
        job_ids = [*ids["jobs"], *await _test_job_ids(session)]
    pg_counts = await _purge_pg(ids, job_ids)
    mongo_counts = await _purge_mongo(ids, job_ids)
    summary = {"pg": pg_counts, "mongo": mongo_counts}
    log.info("demo_data_purged", **summary)
    return summary


async def _run() -> None:
    await purge_demo_data()
    await mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
