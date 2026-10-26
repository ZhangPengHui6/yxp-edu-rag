"""Build versioned Milvus collections and activate the live aliases.

The admin publish flow (``stage`` -> ``commit``) is the normal path, but
``commit_order`` refuses to switch aliases until a sampled evaluation run passes
(``EVAL_RECALL_THRESHOLD``). Evaluation in turn needs published questions, so a
fresh environment that only has ingested chunks cannot bootstrap its first
index through the UI.

This tool performs only the index half of that flow:
``chunks_live`` -> ``chunks_<version>``, Mongo questions -> ``questions_<version>``,
then points ``CHUNKS_ALIAS`` / ``QUESTIONS_ALIAS`` at the new collections. It is
idempotent because the version is timestamped and collections are rebuilt.

Usage (from ``apps/admin-api``)::

    uv run --env-file .env.remote python tools/bootstrap_live_index.py
    uv run --env-file .env.remote python tools/bootstrap_live_index.py --tenant tnt_yxp
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from app.core.config import settings
from app.infra import mongo
from app.infra.milvus import get_vector_index
from app.modules.publish.indexer import build_version_index


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", default="tnt_yxp", help="tenant_id to publish")
    parser.add_argument(
        "--version",
        default="",
        help="index version suffix (default: current UTC timestamp)",
    )
    args = parser.parse_args()

    if not settings.milvus_uri:
        raise SystemExit("MILVUS_URI is empty; refusing to build an in-memory index")

    version = args.version or datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    tenant_id = args.tenant
    print(f"tenant={tenant_id} version={version}")
    print(f"source chunks collection: {settings.milvus_chunks_collection}")

    build = await build_version_index(tenant_id=tenant_id, version=version)
    print(
        f"built chunks_{version}: {build.chunks} chunks; "
        f"questions_{version}: {build.questions} questions"
    )
    if build.embedded_chunks or build.embedded_questions:
        print(
            f"embedded during build: {build.embedded_chunks} chunks / "
            f"{build.embedded_questions} questions ({build.provider})"
        )

    vector = get_vector_index()
    chunk_rows = await vector.stage(f"chunks_{version}", build.chunks)
    question_rows = await vector.stage(f"questions_{version}", build.questions)
    print(f"staged rows: chunks={chunk_rows} questions={question_rows}")

    await vector.commit(settings.chunks_alias, f"chunks_{version}")
    await vector.commit(settings.questions_alias, f"questions_{version}")
    print(
        f"aliases switched: {settings.chunks_alias} -> chunks_{version}, "
        f"{settings.questions_alias} -> questions_{version}"
    )
    print(
        "note: publish_order and its sampled-evaluation gate were bypassed "
        "(no published questions exist yet)"
    )

    await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
