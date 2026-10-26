"""Build the versioned Milvus collections used by the two-phase publish.

``stage_order`` calls :func:`build_version_index` before validating the alias
switch. The builder reads chunks from the Milvus chunk store (the source of
truth for chunks and vectors) and questions that belong to the release
(``approved`` or ``published``) from MongoDB, then rebuilds
``chunks_<version>`` / ``questions_<version>`` from scratch so staging is
idempotent across retries.

With ``MILVUS_URI`` empty (POC / tests) the in-memory adapters are used and
this module only reports the document counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.infra.chunk_store import get_chunk_store
from app.infra.mongo import col
from app.infra.providers import EmbeddingProvider, get_embedding_provider
from app.infra.vectors import (
    dense_to_field,
    has_dense,
    sparse_to_document,
    sparse_to_field,
)

log = get_logger("publish.indexer")

DENSE_DIM = 1024
MAX_TEXT = 64
INSERT_BATCH = 500

# ``commit_order`` promotes ``approved`` to ``published`` only after the alias
# switch. An approved question is therefore part of the release being staged and
# must already be in ``questions_<version>``; selecting only ``published`` would
# make every newly approved question miss the release it ships in.
INDEXABLE_REVIEW = ("approved", "published")


@dataclass(slots=True)
class IndexBuildResult:
    chunks: int = 0
    questions: int = 0
    embedded_chunks: int = 0
    embedded_questions: int = 0
    provider: str = ""


async def build_version_index(*, tenant_id: str, version: str) -> IndexBuildResult:
    """(Re)build ``chunks_<version>`` and ``questions_<version>`` from MongoDB."""
    chunks = await get_chunk_store().all(tenant_id)
    questions = [
        doc
        async for doc in col("questions").find(
            {"tenant_id": tenant_id, "review_status": {"$in": list(INDEXABLE_REVIEW)}}
        )
    ]
    result = IndexBuildResult(chunks=len(chunks), questions=len(questions))
    await _enrich_chunk_metadata(chunks)
    if not settings.milvus_uri:
        log.info(
            "vector_index_memory_mode",
            version=version,
            chunks=len(chunks),
            questions=len(questions),
        )
        return result

    embedder = get_embedding_provider()
    if embedder.name != "unconfigured":
        result.provider = embedder.name
    result.embedded_chunks = await _ensure_chunk_vectors(embedder, chunks)
    result.embedded_questions = await _ensure_question_vectors(embedder, questions)

    from starlette.concurrency import run_in_threadpool

    await run_in_threadpool(_write_collections, version, chunks, questions)
    log.info(
        "vector_index_built",
        version=version,
        chunks=len(chunks),
        questions=len(questions),
        embedded_chunks=result.embedded_chunks,
        embedded_questions=result.embedded_questions,
        provider=result.provider or "cached",
    )
    return result


async def _enrich_chunk_metadata(chunks: list[dict[str, Any]]) -> None:
    """Backfill subject/stage on chunks ingested before they were recorded.

    Older chunk documents only carry ``knowledge_point_ids``; the Milvus schema
    filters on subject/stage, so resolve them through the knowledge points once
    and persist the result on the chunk.
    """
    missing = [
        doc for doc in chunks if not doc.get("subject") or not doc.get("stage")
    ]
    if not missing:
        return
    kp_ids = {
        str(kp_id)
        for doc in missing
        for kp_id in (doc.get("knowledge_point_ids") or [])
    }
    metadata: dict[str, tuple[str, str]] = {}
    if kp_ids:
        async for kp in col("knowledge_points").find(
            {"_id": {"$in": sorted(kp_ids)}}, {"subject": 1, "stage": 1}
        ):
            metadata[str(kp["_id"])] = (
                str(kp.get("subject") or ""),
                str(kp.get("stage") or ""),
            )
    for doc in missing:
        values: dict[str, str] = {}
        for kp_id in doc.get("knowledge_point_ids") or []:
            if doc.get("subject") and doc.get("stage"):
                break
            subject, stage = metadata.get(str(kp_id), ("", ""))
            if subject and not doc.get("subject"):
                doc["subject"] = subject
                values["subject"] = subject
            if stage and not doc.get("stage"):
                doc["stage"] = stage
                values["stage"] = stage
        if values:
            await get_chunk_store().update_fields(doc["_id"], values)


async def _ensure_chunk_vectors(
    embedder: EmbeddingProvider, chunks: list[dict[str, Any]]
) -> int:
    missing = [
        doc
        for doc in chunks
        if not has_dense(doc.get("dense_vector"), DENSE_DIM)
        or not isinstance(doc.get("sparse_vector"), dict)
    ]
    if not missing:
        return 0
    if embedder.name == "unconfigured":
        raise ProviderUnavailable(
            f"{len(missing)} 个片段缺少向量，且 EMBEDDING_PROVIDER 未启用；"
            "请设置 EMBEDDING_PROVIDER=bge_m3 后重试"
        )
    batch = await embedder.embed_documents([str(doc.get("content", "")) for doc in missing])
    if len(batch) != len(missing):
        raise ProviderUnavailable(
            f"{embedder.name} 返回 {len(batch)} 个片段向量，期望 {len(missing)}"
        )
    for doc, dense, sparse in zip(missing, batch.dense, batch.sparse, strict=True):
        values = {
            "dense_vector": [float(value) for value in dense],
            "sparse_vector": sparse_to_document(sparse),
            "embedding_model": embedder.name,
        }
        doc.update(values)
    await get_chunk_store().upsert(missing)
    return len(missing)


async def _ensure_question_vectors(
    embedder: EmbeddingProvider, questions: list[dict[str, Any]]
) -> int:
    missing = [
        doc
        for doc in questions
        if not has_dense(doc.get("stem_vector"), DENSE_DIM)
        or not has_dense(doc.get("solution_vector"), DENSE_DIM)
        or not isinstance(doc.get("sparse_vector"), dict)
    ]
    if not missing:
        return 0
    if embedder.name == "unconfigured":
        raise ProviderUnavailable(
            f"{len(missing)} 道已发布题目缺少向量，且 EMBEDDING_PROVIDER 未启用；"
            "请设置 EMBEDDING_PROVIDER=bge_m3 后重试"
        )

    stem_batch = await embedder.embed_documents([str(doc.get("stem", "")) for doc in missing])
    if len(stem_batch) != len(missing):
        raise ProviderUnavailable(
            f"{embedder.name} 返回 {len(stem_batch)} 个题干向量，期望 {len(missing)}"
        )
    solution_texts = [
        str(doc.get("analysis") or doc.get("answer") or "") for doc in missing
    ]
    solution_indexes = [index for index, text in enumerate(solution_texts) if text.strip()]
    solutions = None
    if solution_indexes:
        solutions = await embedder.embed_documents(
            [solution_texts[index] for index in solution_indexes]
        )
        if len(solutions) != len(solution_indexes):
            raise ProviderUnavailable(
                f"{embedder.name} 返回 {len(solutions)} 个解答向量，"
                f"期望 {len(solution_indexes)}"
            )
    solution_vectors: list[list[float]] = [[0.0] * DENSE_DIM for _ in missing]
    if solutions is not None:
        for position, index in enumerate(solution_indexes):
            solution_vectors[index] = [float(value) for value in solutions.dense[position]]

    for index, (doc, dense, sparse) in enumerate(
        zip(missing, stem_batch.dense, stem_batch.sparse, strict=True)
    ):
        values = {
            "stem_vector": [float(value) for value in dense],
            "solution_vector": solution_vectors[index],
            "sparse_vector": sparse_to_document(sparse),
            "embedding_model": embedder.name,
        }
        doc.update(values)
        await col("questions").update_one({"_id": doc["_id"]}, {"$set": values})
    return len(missing)


def _write_collections(
    version: str, chunks: list[dict[str, Any]], questions: list[dict[str, Any]]
) -> None:
    try:
        from pymilvus import MilvusClient
    except ImportError as exc:  # pragma: no cover - optional extra
        raise ProviderUnavailable("Milvus 适配需要先执行 `uv sync --extra milvus`") from exc

    client = None
    try:
        client = MilvusClient(uri=settings.milvus_uri)
        _recreate(
            client,
            f"chunks_{version}",
            _chunk_schema,
            [_chunk_row(doc) for doc in chunks],
        )
        _recreate(
            client,
            f"questions_{version}",
            _question_schema,
            [_question_row(doc) for doc in questions],
        )
    except ProviderUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - pymilvus raises many types
        raise ProviderUnavailable(f"Milvus 索引构建失败：{exc}") from exc
    finally:
        if client is not None:
            client.close()


def _recreate(client, name: str, schema_builder, rows: list[dict[str, Any]]) -> None:
    from pymilvus import MilvusClient

    if client.has_collection(name):
        client.drop_collection(name)
    index_params = MilvusClient.prepare_index_params()
    index_params.add_index(
        field_name="dense_vector" if name.startswith("chunks_") else "stem_vector",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )
    if name.startswith("questions_"):
        index_params.add_index(
            field_name="solution_vector",
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
    index_params.add_index(
        field_name="sparse_vector",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="IP",
    )
    client.create_collection(
        collection_name=name,
        schema=schema_builder(),
        index_params=index_params,
    )
    for start in range(0, len(rows), INSERT_BATCH):
        client.insert(collection_name=name, data=rows[start : start + INSERT_BATCH])
    client.flush(name)


def _chunk_schema():
    from pymilvus import DataType, MilvusClient

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field("tenant_id", DataType.VARCHAR, max_length=64)
    schema.add_field("book_id", DataType.VARCHAR, max_length=64)
    schema.add_field("book_title", DataType.VARCHAR, max_length=255)
    schema.add_field("chapter_id", DataType.VARCHAR, max_length=64)
    schema.add_field("subject", DataType.VARCHAR, max_length=64)
    schema.add_field("stage", DataType.VARCHAR, max_length=32)
    schema.add_field("grade", DataType.VARCHAR, max_length=32)
    schema.add_field(
        "kp_ids",
        DataType.ARRAY,
        element_type=DataType.VARCHAR,
        max_capacity=32,
        max_length=64,
    )
    schema.add_field("content_type", DataType.VARCHAR, max_length=32)
    schema.add_field("content", DataType.VARCHAR, max_length=8192)
    schema.add_field("page_start", DataType.INT32)
    schema.add_field("page_end", DataType.INT32)
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=DENSE_DIM)
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
    return schema


def _question_schema():
    from pymilvus import DataType, MilvusClient

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("question_id", DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field("tenant_id", DataType.VARCHAR, max_length=64)
    schema.add_field("subject", DataType.VARCHAR, max_length=64)
    schema.add_field("stage", DataType.VARCHAR, max_length=32)
    schema.add_field("grade", DataType.VARCHAR, max_length=32)
    schema.add_field("edition", DataType.VARCHAR, max_length=64)
    schema.add_field("chapter_id", DataType.VARCHAR, max_length=64)
    schema.add_field(
        "kp_ids",
        DataType.ARRAY,
        element_type=DataType.VARCHAR,
        max_capacity=32,
        max_length=64,
    )
    schema.add_field("question_type", DataType.VARCHAR, max_length=32)
    schema.add_field("difficulty", DataType.VARCHAR, max_length=16)
    schema.add_field("review_status", DataType.VARCHAR, max_length=16)
    # Text columns: retrieval needs the stem/answer to build grounded context and
    # citations. Vectors alone cannot be turned back into readable evidence.
    schema.add_field("stem", DataType.VARCHAR, max_length=8192)
    schema.add_field("options", DataType.VARCHAR, max_length=2048)
    schema.add_field("answer", DataType.VARCHAR, max_length=2048)
    schema.add_field("analysis", DataType.VARCHAR, max_length=8192)
    schema.add_field("stem_vector", DataType.FLOAT_VECTOR, dim=DENSE_DIM)
    schema.add_field("solution_vector", DataType.FLOAT_VECTOR, dim=DENSE_DIM)
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
    return schema


def _chunk_row(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": _text(doc.get("_id"), MAX_TEXT),
        "tenant_id": _text(doc.get("tenant_id"), MAX_TEXT),
        "book_id": _text(doc.get("book_id"), MAX_TEXT),
        "book_title": _text(doc.get("book_title"), 255),
        "chapter_id": _text(doc.get("chapter_id"), MAX_TEXT),
        "subject": _text(doc.get("subject"), MAX_TEXT),
        "stage": _text(doc.get("stage"), 32),
        "grade": _text(doc.get("grade"), 32),
        "kp_ids": _ids(doc.get("knowledge_point_ids")),
        "content_type": _text(doc.get("content_type") or "text", 32),
        "content": _text(doc.get("content"), 8192),
        "page_start": _int(doc.get("page")),
        "page_end": _int(doc.get("page_end") or doc.get("page")),
        "dense_vector": dense_to_field(doc.get("dense_vector"), DENSE_DIM),
        "sparse_vector": sparse_to_field(doc.get("sparse_vector")),
    }


def _question_row(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": _text(doc.get("_id"), MAX_TEXT),
        "tenant_id": _text(doc.get("tenant_id"), MAX_TEXT),
        "subject": _text(doc.get("subject"), MAX_TEXT),
        "stage": _text(doc.get("stage"), 32),
        "grade": _text(doc.get("grade"), 32),
        "edition": _text(doc.get("edition"), MAX_TEXT),
        "chapter_id": _text(doc.get("chapter_id"), MAX_TEXT),
        "kp_ids": _ids(doc.get("knowledge_point_ids")),
        "question_type": _text(doc.get("type"), 32),
        "difficulty": _text(doc.get("difficulty") or "medium", 16),
        # Milvus rows are served only through the active alias, and every row in
        # a staged release is published by the time that alias points at it.
        "review_status": "published",
        "stem": _text(doc.get("stem"), 8192),
        "options": _text(_options_text(doc.get("options")), 2048),
        "answer": _text(doc.get("answer"), 2048),
        "analysis": _text(doc.get("analysis"), 8192),
        "stem_vector": dense_to_field(doc.get("stem_vector"), DENSE_DIM),
        "solution_vector": dense_to_field(doc.get("solution_vector"), DENSE_DIM),
        "sparse_vector": sparse_to_field(doc.get("sparse_vector")),
    }


def _text(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


def _options_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key}. {text}" for key, text in value.items())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return "" if value is None else str(value)


def _ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:MAX_TEXT] for item in value if item][:32]


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
