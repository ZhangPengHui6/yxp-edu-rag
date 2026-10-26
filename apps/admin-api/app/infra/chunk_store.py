"""Chunk storage: Milvus is the source of truth for chunks and their vectors.

Ingestion writes chunks straight into the Milvus collection configured by
``MILVUS_CHUNKS_COLLECTION`` (default ``chunks_live``), mirroring the reference
ingestion graph (``NodeImportMilvus``). MongoDB keeps textbooks, chapters,
knowledge points, questions and annotation blocks, but no chunks.

When ``MILVUS_URI`` is empty (POC / tests) an in-process store exposes the same
interface so the API and the publish indexer keep working without Milvus.
"""

from __future__ import annotations

from typing import Any, Protocol

from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.infra.vectors import dense_to_field, sparse_to_field

DENSE_DIM = 1024
CONTENT_MAX = 8192
INSERT_BATCH = 500

# Documents exchanged with the rest of the app keep the MongoDB-style shape so
# nodes, coverage and the publish indexer need no translation layer.
DOC_FIELDS = [
    "chunk_id",
    "tenant_id",
    "job_id",
    "book_id",
    "book_title",
    "edition",
    "subject",
    "stage",
    "grade",
    "kp_ids",
    "content_type",
    "content",
    "location",
    "page_start",
    "page_end",
    "dense_vector",
    "sparse_vector",
]

SEARCH_FIELDS = [
    "chunk_id",
    "tenant_id",
    "job_id",
    "book_id",
    "book_title",
    "edition",
    "subject",
    "stage",
    "grade",
    "kp_ids",
    "content_type",
    "content",
    "location",
    "page_start",
    "page_end",
]


class ChunkStore(Protocol):
    async def upsert(self, docs: list[dict[str, Any]]) -> int:
        ...

    async def delete_job(self, tenant_id: str, job_id: str) -> None:
        ...

    async def delete_jobs(self, job_ids: list[str]) -> int:
        ...

    async def delete_ids(self, chunk_ids: list[str]) -> int:
        ...

    async def all(self, tenant_id: str) -> list[dict[str, Any]]:
        ...

    async def count(self, tenant_id: str) -> int:
        ...

    async def count_by_job(self, tenant_id: str, job_id: str) -> int:
        ...

    async def evidence(self, tenant_id: str, node_id: str, limit: int = 10) -> list[dict[str, Any]]:
        ...

    async def has_evidence(self, tenant_id: str, node_ids: list[str]) -> bool:
        ...

    async def kp_counts(self, tenant_id: str, node_ids: list[str]) -> dict[str, int]:
        ...

    async def references(self, tenant_id: str) -> list[dict[str, Any]]:
        ...

    async def update_fields(self, chunk_id: str, values: dict[str, Any]) -> None:
        ...

    def clear(self) -> None:
        ...


# ---------------------------------------------------------------------------


class MemoryChunkStore:
    """Process-local store used for tests and offline POC runs."""

    def __init__(self) -> None:
        self._docs: dict[str, dict[str, Any]] = {}

    def clear(self) -> None:
        self._docs.clear()

    async def upsert(self, docs: list[dict[str, Any]]) -> int:
        for doc in docs:
            self._docs[str(doc["_id"])] = dict(doc)
        return len(docs)

    async def delete_job(self, tenant_id: str, job_id: str) -> None:
        stale = [
            key
            for key, doc in self._docs.items()
            if doc.get("tenant_id") == tenant_id and doc.get("job_id") == job_id
        ]
        for key in stale:
            self._docs.pop(key, None)

    async def delete_jobs(self, job_ids: list[str]) -> int:
        wanted = {str(item) for item in job_ids if item}
        stale = [
            key for key, doc in self._docs.items() if str(doc.get("job_id") or "") in wanted
        ]
        for key in stale:
            self._docs.pop(key, None)
        return len(stale)

    async def delete_ids(self, chunk_ids: list[str]) -> int:
        removed = 0
        for chunk_id in chunk_ids:
            if self._docs.pop(str(chunk_id), None) is not None:
                removed += 1
        return removed

    async def all(self, tenant_id: str) -> list[dict[str, Any]]:
        return [dict(doc) for doc in self._docs.values() if doc.get("tenant_id") == tenant_id]

    async def count(self, tenant_id: str) -> int:
        return sum(1 for doc in self._docs.values() if doc.get("tenant_id") == tenant_id)

    async def count_by_job(self, tenant_id: str, job_id: str) -> int:
        return sum(
            1
            for doc in self._docs.values()
            if doc.get("tenant_id") == tenant_id and doc.get("job_id") == job_id
        )

    async def evidence(self, tenant_id: str, node_id: str, limit: int = 10) -> list[dict[str, Any]]:
        hits = [
            dict(doc)
            for doc in self._docs.values()
            if doc.get("tenant_id") == tenant_id
            and node_id in (doc.get("knowledge_point_ids") or [])
        ]
        return hits[:limit]

    async def has_evidence(self, tenant_id: str, node_ids: list[str]) -> bool:
        wanted = set(node_ids)
        return any(
            doc.get("tenant_id") == tenant_id
            and wanted.intersection(doc.get("knowledge_point_ids") or [])
            for doc in self._docs.values()
        )

    async def kp_counts(self, tenant_id: str, node_ids: list[str]) -> dict[str, int]:
        wanted = set(node_ids)
        counts: dict[str, int] = {node_id: 0 for node_id in wanted}
        for doc in self._docs.values():
            if doc.get("tenant_id") != tenant_id:
                continue
            for kp_id in set(doc.get("knowledge_point_ids") or []) & wanted:
                counts[kp_id] += 1
        return counts

    async def references(self, tenant_id: str) -> list[dict[str, Any]]:
        return [
            {"_id": doc["_id"], "knowledge_point_ids": doc.get("knowledge_point_ids") or []}
            for doc in self._docs.values()
            if doc.get("tenant_id") == tenant_id
        ]

    async def update_fields(self, chunk_id: str, values: dict[str, Any]) -> None:
        doc = self._docs.get(str(chunk_id))
        if doc is not None:
            doc.update(values)


class MilvusChunkStore:
    """Milvus-backed chunk store (dense + sparse vectors live here)."""

    def __init__(self) -> None:
        self.collection = settings.milvus_chunks_collection

    def clear(self) -> None:  # pragma: no cover - destructive, tests never call it
        raise ProviderUnavailable("Milvus 存储不支持进程内清空，请按 job / 文档删除")

    def _client(self):
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ProviderUnavailable("Milvus 适配需要先执行 `uv sync --extra milvus`") from exc
        return MilvusClient(uri=settings.milvus_uri)

    def _with_client(self, fn):
        client = None
        try:
            client = self._client()
            self._ensure_collection(client)
            return fn(client)
        except ProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - pymilvus raises many types
            raise ProviderUnavailable(f"Milvus chunk 存储失败：{exc}") from exc
        finally:
            if client is not None:
                client.close()

    def _ensure_collection(self, client) -> None:
        if client.has_collection(self.collection):
            return
        from pymilvus import DataType, MilvusClient

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("tenant_id", DataType.VARCHAR, max_length=64)
        schema.add_field("job_id", DataType.VARCHAR, max_length=64)
        schema.add_field("book_id", DataType.VARCHAR, max_length=64)
        schema.add_field("book_title", DataType.VARCHAR, max_length=255)
        schema.add_field("edition", DataType.VARCHAR, max_length=64)
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
        schema.add_field("content", DataType.VARCHAR, max_length=CONTENT_MAX)
        schema.add_field("location", DataType.VARCHAR, max_length=64)
        schema.add_field("page_start", DataType.INT32)
        schema.add_field("page_end", DataType.INT32)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=DENSE_DIM)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)

        index_params = MilvusClient.prepare_index_params()
        index_params.add_index(
            field_name="dense_vector",
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
            collection_name=self.collection,
            schema=schema,
            index_params=index_params,
        )

    async def upsert(self, docs: list[dict[str, Any]]) -> int:
        if not docs:
            return 0
        rows = [self._row(doc) for doc in docs]

        def _write(client) -> int:
            for start in range(0, len(rows), INSERT_BATCH):
                client.upsert(collection_name=self.collection, data=rows[start : start + INSERT_BATCH])
            client.flush(self.collection)
            return len(rows)

        return await run_in_threadpool(self._with_client, _write)

    async def delete_job(self, tenant_id: str, job_id: str) -> None:
        if not tenant_id or not job_id:
            return
        filter_expr = f'tenant_id == {_quote(tenant_id)} and job_id == {_quote(job_id)}'

        def _delete(client) -> None:
            client.delete(collection_name=self.collection, filter=filter_expr)

        await run_in_threadpool(self._with_client, _delete)

    async def delete_jobs(self, job_ids: list[str]) -> int:
        ids = [str(item) for item in job_ids if item]
        if not ids:
            return 0
        filter_expr = f"job_id in [{', '.join(_quote(item) for item in ids)}]"

        def _delete(client) -> int:
            result = client.delete(collection_name=self.collection, filter=filter_expr)
            return int(result.get("delete_count", 0) or 0)

        return await run_in_threadpool(self._with_client, _delete)

    async def delete_ids(self, chunk_ids: list[str]) -> int:
        ids = [str(item) for item in chunk_ids if item]
        if not ids:
            return 0
        filter_expr = f"chunk_id in [{', '.join(_quote(item) for item in ids)}]"

        def _delete(client) -> int:
            result = client.delete(collection_name=self.collection, filter=filter_expr)
            return int(result.get("delete_count", 0) or 0)

        return await run_in_threadpool(self._with_client, _delete)

    async def all(self, tenant_id: str) -> list[dict[str, Any]]:
        def _read(client) -> list[dict[str, Any]]:
            iterator = client.query_iterator(
                collection_name=self.collection,
                batch_size=1000,
                filter=f"tenant_id == {_quote(tenant_id)}",
                output_fields=DOC_FIELDS,
            )
            docs: list[dict[str, Any]] = []
            try:
                while True:
                    rows = iterator.next()
                    if not rows:
                        break
                    docs.extend(_doc(row) for row in rows)
            finally:
                iterator.close()
            return docs

        return await run_in_threadpool(self._with_client, _read)

    async def count(self, tenant_id: str) -> int:
        return await self._count(f"tenant_id == {_quote(tenant_id)}")

    async def count_by_job(self, tenant_id: str, job_id: str) -> int:
        return await self._count(
            f"tenant_id == {_quote(tenant_id)} and job_id == {_quote(job_id)}"
        )

    async def _count(self, filter_expr: str) -> int:
        def _read(client) -> int:
            rows = client.query(
                collection_name=self.collection,
                filter=filter_expr,
                output_fields=["count(*)"],
            )
            if not rows:
                return 0
            return int(rows[0].get("count(*)", 0))

        return await run_in_threadpool(self._with_client, _read)

    async def evidence(self, tenant_id: str, node_id: str, limit: int = 10) -> list[dict[str, Any]]:
        def _read(client) -> list[dict[str, Any]]:
            rows = client.query(
                collection_name=self.collection,
                filter=(
                    f"tenant_id == {_quote(tenant_id)} and "
                    f"ARRAY_CONTAINS(kp_ids, {_quote(node_id)})"
                ),
                output_fields=SEARCH_FIELDS,
                limit=limit,
            )
            return [_doc(row) for row in rows]

        return await run_in_threadpool(self._with_client, _read)

    async def has_evidence(self, tenant_id: str, node_ids: list[str]) -> bool:
        if not node_ids:
            return False
        match = " or ".join(
            f"ARRAY_CONTAINS(kp_ids, {_quote(node_id)})" for node_id in node_ids
        )
        return await self._count(f"tenant_id == {_quote(tenant_id)} and ({match})") > 0

    async def kp_counts(self, tenant_id: str, node_ids: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node_id in node_ids:
            counts[node_id] = await self._count(
                f"tenant_id == {_quote(tenant_id)} and "
                f"ARRAY_CONTAINS(kp_ids, {_quote(node_id)})"
            )
        return counts

    async def references(self, tenant_id: str) -> list[dict[str, Any]]:
        def _read(client) -> list[dict[str, Any]]:
            iterator = client.query_iterator(
                collection_name=self.collection,
                batch_size=1000,
                filter=f"tenant_id == {_quote(tenant_id)}",
                output_fields=["chunk_id", "kp_ids"],
            )
            rows: list[dict[str, Any]] = []
            try:
                while True:
                    batch = iterator.next()
                    if not batch:
                        break
                    rows.extend(
                        {"_id": row["chunk_id"], "knowledge_point_ids": row.get("kp_ids") or []}
                        for row in batch
                    )
            finally:
                iterator.close()
            return rows

        return await run_in_threadpool(self._with_client, _read)

    async def update_fields(self, chunk_id: str, values: dict[str, Any]) -> None:
        if not values:
            return

        def _update(client) -> None:
            rows = client.query(
                collection_name=self.collection,
                filter=f"chunk_id == {_quote(chunk_id)}",
                output_fields=DOC_FIELDS,
            )
            if not rows:
                return
            doc = _doc(rows[0])
            doc.update(values)
            client.upsert(collection_name=self.collection, data=[_row_from_doc(doc)])
            client.flush(self.collection)

        await run_in_threadpool(self._with_client, _update)

    def _row(self, doc: dict[str, Any]) -> dict[str, Any]:
        try:
            dense = dense_to_field(doc.get("dense_vector"), DENSE_DIM)
        except ValueError as exc:
            raise ProviderUnavailable(
                f"片段 {doc.get('_id')} 缺少 {DENSE_DIM} 维 dense 向量，无法写入 Milvus"
            ) from exc
        return _row_from_doc(doc, dense=dense)


def _row_from_doc(doc: dict[str, Any], dense: list[float] | None = None) -> dict[str, Any]:
    content = str(doc.get("content") or doc.get("evidence") or "")
    return {
        "chunk_id": str(doc.get("_id") or doc.get("chunk_id") or "")[:64],
        "tenant_id": str(doc.get("tenant_id") or "")[:64],
        "job_id": str(doc.get("job_id") or "")[:64],
        "book_id": str(doc.get("book_id") or "")[:64],
        "book_title": str(doc.get("book_title") or "")[:255],
        "edition": str(doc.get("edition") or "")[:64],
        "subject": str(doc.get("subject") or "")[:64],
        "stage": str(doc.get("stage") or "")[:32],
        "grade": str(doc.get("grade") or "")[:32],
        "kp_ids": [str(item)[:64] for item in (doc.get("knowledge_point_ids") or [])][:32],
        "content_type": str(doc.get("content_type") or "text")[:32],
        "content": content[:CONTENT_MAX],
        "location": str(doc.get("location") or "")[:64],
        "page_start": int(doc.get("page") or doc.get("page_start") or 0),
        "page_end": int(doc.get("page_end") or doc.get("page") or 0),
        "dense_vector": dense
        if dense is not None
        else dense_to_field(doc.get("dense_vector"), DENSE_DIM),
        "sparse_vector": sparse_to_field(doc.get("sparse_vector")),
    }


def _doc(row: dict[str, Any]) -> dict[str, Any]:
    content = str(row.get("content") or "")
    return {
        "_id": row.get("chunk_id", ""),
        "tenant_id": row.get("tenant_id", ""),
        "job_id": row.get("job_id", ""),
        "book_id": row.get("book_id", ""),
        "book_title": row.get("book_title", ""),
        "edition": row.get("edition", ""),
        "subject": row.get("subject", ""),
        "stage": row.get("stage", ""),
        "grade": row.get("grade", ""),
        "knowledge_point_ids": list(row.get("kp_ids") or []),
        "content_type": row.get("content_type", "text"),
        "content": content,
        "evidence": content,
        "location": row.get("location", ""),
        "page": int(row.get("page_start") or 0),
        "page_end": int(row.get("page_end") or row.get("page_start") or 0),
        "dense_vector": [float(value) for value in (row.get("dense_vector") or [])],
        "sparse_vector": dict(row.get("sparse_vector") or {}),
    }


def _quote(value: Any) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


_store: ChunkStore | None = None


def get_chunk_store() -> ChunkStore:
    global _store
    if _store is None:
        _store = MilvusChunkStore() if settings.milvus_uri else MemoryChunkStore()
    return _store
