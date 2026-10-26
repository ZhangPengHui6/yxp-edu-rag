"""Milvus hybrid retrieval over the published chunk and question aliases.

Chunks and questions are searched with the same BGE-M3 query embedding: chunks
contribute textbook prose, questions contribute stems and worked solutions.
Both are normalised into the same evidence shape (``source`` / ``content``) so
RRF, rerank and prompt assembly stay channel-agnostic.

Dense (HNSW/COSINE) and sparse (inverted/IP) fields are queried for each alias.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.infra.providers import get_embedding_provider
from starlette.concurrency import run_in_threadpool

CHUNK_FIELDS = [
    "chunk_id",
    "book_id",
    "book_title",
    "chapter_id",
    "subject",
    "stage",
    "grade",
    "kp_ids",
    "content_type",
    "content",
    "page_start",
    "page_end",
]

QUESTION_FIELDS = [
    "question_id",
    "subject",
    "stage",
    "grade",
    "question_type",
    "difficulty",
    "stem",
    "options",
    "answer",
    "analysis",
]

OPTION_LETTERS = "ABCDEFGH"

_client: Any = None
_provider: Any = None


def _embedding():
    """Cache the embedding provider: constructing it per request would reload BGE-M3."""
    global _provider
    if _provider is None:
        _provider = get_embedding_provider()
    return _provider


def _milvus():
    global _client
    if _client is None:
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ProviderUnavailable("Milvus 适配需要先安装 pymilvus") from exc
        if not settings.milvus_uri:
            raise ProviderUnavailable("MILVUS_URI 未配置，无法检索")
        _client = MilvusClient(uri=settings.milvus_uri)
    return _client


def _quote(value: Any) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def build_filter(
    tenant_id: str,
    *,
    subject: str = "",
    stage: str = "",
    grade: str = "",
    book_id: str = "",
) -> str:
    clauses = [f"tenant_id == {_quote(tenant_id)}"]
    for field, value in (
        ("subject", subject),
        ("stage", stage),
        ("grade", grade),
        ("book_id", book_id),
    ):
        if value:
            clauses.append(f"{field} == {_quote(value)}")
    return " and ".join(clauses)


async def embed_query(query: str) -> tuple[list[float], dict[int, float]]:
    provider = _embedding()
    if provider.name == "unconfigured":
        raise ProviderUnavailable("EMBEDDING_PROVIDER 未启用，无法生成查询向量")
    batch = await provider.embed_documents([query])
    if not batch.dense:
        raise ProviderUnavailable(f"{provider.name} 未返回查询向量")
    sparse = batch.sparse[0] if batch.sparse else {}
    return [float(value) for value in batch.dense[0]], {
        int(key): float(value) for key, value in sparse.items()
    }


def _search(
    *,
    collection: str,
    fields: list[str],
    data: Any,
    field: str,
    metric: str,
    limit: int,
    filter_expr: str,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        result = _milvus().search(
            collection_name=collection,
            data=[data],
            anns_field=field,
            limit=limit,
            filter=filter_expr,
            output_fields=fields,
            search_params={"metric_type": metric, "params": params},
        )
    except ProviderUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - pymilvus raises many types
        raise ProviderUnavailable(
            f"Milvus 检索失败（{field} / {collection}）：{exc}"
        ) from exc

    hits: list[dict[str, Any]] = []
    for hit in result[0]:
        row = {field_name: hit.get(field_name) for field_name in fields}
        row["score"] = float(hit.get("distance", 0.0))
        hits.append(row)
    return hits


def _chunk_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "chunk",
        "chunk_id": row.get("chunk_id") or "",
        "question_id": "",
        "book_id": row.get("book_id") or "",
        "book_title": row.get("book_title") or "",
        "subject": row.get("subject") or "",
        "stage": row.get("stage") or "",
        "grade": row.get("grade") or "",
        "question_type": "",
        "difficulty": "",
        "content_type": row.get("content_type") or "",
        "content": row.get("content") or "",
        "page_start": int(row.get("page_start") or 0),
        "page_end": int(row.get("page_end") or 0),
        "score": float(row.get("score", 0.0)),
    }


def _question_content(row: dict[str, Any]) -> str:
    stem = str(row.get("stem") or "").strip()
    options = row.get("options") or []
    if isinstance(options, str):
        options_text = options.strip()
    else:
        options_text = "；".join(
            f"{OPTION_LETTERS[index]}. {str(text).strip()}"
            for index, text in enumerate(options)
            if str(text).strip()
        )
    answer = str(row.get("answer") or "").strip()
    analysis = str(row.get("analysis") or "").strip()
    parts: list[str] = []
    if stem:
        parts.append(f"题目：{stem}")
    if options_text:
        parts.append(f"选项：{options_text}")
    if answer:
        parts.append(f"答案：{answer}")
    if analysis:
        parts.append(f"解析：{analysis}")
    return "\n".join(parts)


def _question_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "question",
        "chunk_id": "",
        "question_id": row.get("question_id") or "",
        "book_id": "",
        "book_title": "",
        "subject": row.get("subject") or "",
        "stage": row.get("stage") or "",
        "grade": row.get("grade") or "",
        "question_type": row.get("question_type") or "",
        "difficulty": row.get("difficulty") or "",
        "content_type": "question",
        "content": _question_content(row),
        "page_start": 0,
        "page_end": 0,
        "score": float(row.get("score", 0.0)),
    }


def _chunk_dense(
    vector: list[float], *, filter_expr: str, limit: int
) -> list[dict[str, Any]]:
    rows = _search(
        collection=settings.chunks_alias,
        fields=CHUNK_FIELDS,
        data=vector,
        field="dense_vector",
        metric="COSINE",
        limit=limit,
        filter_expr=filter_expr,
        params={"ef": max(64, limit * 4)},
    )
    return [_chunk_row(row) for row in rows]


def _chunk_sparse(
    vector: dict[int, float], *, filter_expr: str, limit: int
) -> list[dict[str, Any]]:
    rows = _search(
        collection=settings.chunks_alias,
        fields=CHUNK_FIELDS,
        data=vector,
        field="sparse_vector",
        metric="IP",
        limit=limit,
        filter_expr=filter_expr,
        params={},
    )
    return [_chunk_row(row) for row in rows]


def _question_dense(
    vector: list[float], *, filter_expr: str, limit: int
) -> list[dict[str, Any]]:
    rows = _search(
        collection=settings.questions_alias,
        fields=QUESTION_FIELDS,
        data=vector,
        field="stem_vector",
        metric="COSINE",
        limit=limit,
        filter_expr=filter_expr,
        params={"ef": max(64, limit * 4)},
    )
    return [_question_row(row) for row in rows]


def _question_sparse(
    vector: dict[int, float], *, filter_expr: str, limit: int
) -> list[dict[str, Any]]:
    rows = _search(
        collection=settings.questions_alias,
        fields=QUESTION_FIELDS,
        data=vector,
        field="sparse_vector",
        metric="IP",
        limit=limit,
        filter_expr=filter_expr,
        params={},
    )
    return [_question_row(row) for row in rows]


async def dense_search(
    vector: list[float], *, filter_expr: str, limit: int
) -> list[dict[str, Any]]:
    return await run_in_threadpool(
        _chunk_dense, vector, filter_expr=filter_expr, limit=limit
    )


async def sparse_search(
    vector: dict[int, float], *, filter_expr: str, limit: int
) -> list[dict[str, Any]]:
    if not vector:
        return []
    return await run_in_threadpool(
        _chunk_sparse, vector, filter_expr=filter_expr, limit=limit
    )


async def question_dense_search(
    vector: list[float], *, filter_expr: str, limit: int
) -> list[dict[str, Any]]:
    return await run_in_threadpool(
        _question_dense, vector, filter_expr=filter_expr, limit=limit
    )


async def question_sparse_search(
    vector: dict[int, float], *, filter_expr: str, limit: int
) -> list[dict[str, Any]]:
    if not vector:
        return []
    return await run_in_threadpool(
        _question_sparse, vector, filter_expr=filter_expr, limit=limit
    )


async def hybrid_retrieve(
    query: str,
    *,
    tenant_id: str,
    subject: str = "",
    stage: str = "",
    grade: str = "",
    book_id: str = "",
    top_k: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return chunk dense, chunk sparse, question dense, question sparse hits."""
    dense_vector, sparse_vector = await embed_query(query)
    chunk_filter = build_filter(
        tenant_id, subject=subject, stage=stage, grade=grade, book_id=book_id
    )
    # Questions carry no book_id, so the textbook filter only applies to chunks.
    question_filter = build_filter(
        tenant_id, subject=subject, stage=stage, grade=grade
    )
    return await asyncio.gather(
        dense_search(dense_vector, filter_expr=chunk_filter, limit=top_k),
        sparse_search(sparse_vector, filter_expr=chunk_filter, limit=top_k),
        question_dense_search(dense_vector, filter_expr=question_filter, limit=top_k),
        question_sparse_search(sparse_vector, filter_expr=question_filter, limit=top_k),
    )
