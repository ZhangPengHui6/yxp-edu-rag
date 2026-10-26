"""Vector-index alias manager.

Admin publish is a two-phase alias switch (总纲 ADR-5):
``stage`` prepares/validates the new collection, ``commit`` atomically moves the
alias, ``rollback`` points it back. Embeddings are generated during ingestion and
by ``app.modules.publish.indexer``; this module only validates and switches
aliases.
"""

from __future__ import annotations

from typing import Protocol

from app.core.config import settings
from app.core.errors import ProviderUnavailable


class VectorIndex(Protocol):
    async def health(self) -> dict:
        ...

    async def stage(self, version: str, expected_count: int) -> int:
        ...

    async def commit(self, alias: str, version: str) -> None:
        ...

    async def rollback(self, alias: str, version: str) -> None:
        ...

    async def current(self, alias: str) -> str | None:
        ...


class MemoryVectorIndex:
    """In-process adapter used when MILVUS_URI is empty (POC / tests)."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._aliases: dict[str, str] = {}
        self._history: dict[str, list[str]] = {}

    async def health(self) -> dict:
        return {"backend": "memory", "ok": True, "detail": "in-process alias manager"}

    async def stage(self, version: str, expected_count: int) -> int:
        self._counts[version] = expected_count
        return expected_count

    async def commit(self, alias: str, version: str) -> None:
        previous = self._aliases.get(alias)
        if previous and previous != version:
            self._history.setdefault(alias, []).append(previous)
        self._aliases[alias] = version

    async def rollback(self, alias: str, version: str) -> None:
        self._aliases[alias] = version

    async def current(self, alias: str) -> str | None:
        return self._aliases.get(alias)

    def count(self, version: str) -> int:
        return self._counts.get(version, 0)


class MilvusVectorIndex:
    """Thin Milvus alias manager.

    ``stage`` verifies that the collection produced by the publish index builder
    exists and returns its row count; it intentionally does not insert embeddings.
    """

    def _client(self):
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ProviderUnavailable("Milvus adapter requires `pip install .[milvus]`") from exc
        return MilvusClient(uri=settings.milvus_uri)

    def _run(self, fn, *args):
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailable(f"Milvus 不可用: {exc}") from exc

    async def health(self) -> dict:
        from starlette.concurrency import run_in_threadpool

        try:
            collections = await run_in_threadpool(lambda: self._client().list_collections())
            return {"backend": "milvus", "ok": True, "detail": f"{len(collections)} collections"}
        except Exception as exc:  # noqa: BLE001
            return {"backend": "milvus", "ok": False, "detail": str(exc)}

    async def stage(self, version: str, expected_count: int) -> int:
        from starlette.concurrency import run_in_threadpool

        def _check() -> int:
            client = self._client()
            if not client.has_collection(version):
                raise ProviderUnavailable(f"索引集合 {version} 不存在，请先完成索引构建")
            stats = client.get_collection_stats(version)
            return int(stats.get("row_count", expected_count))

        return await run_in_threadpool(_check)

    async def commit(self, alias: str, version: str) -> None:
        from starlette.concurrency import run_in_threadpool

        def _switch() -> None:
            client = self._client()
            if client.has_collection(alias) or _alias_exists(client, alias):
                client.alter_alias(collection_name=version, alias=alias)
            else:
                client.create_alias(collection_name=version, alias=alias)

        await run_in_threadpool(_switch)

    async def rollback(self, alias: str, version: str) -> None:
        await self.commit(alias, version)

    async def current(self, alias: str) -> str | None:
        from starlette.concurrency import run_in_threadpool

        def _describe() -> str | None:
            client = self._client()
            try:
                info = client.describe_alias(alias)
            except Exception:  # noqa: BLE001
                return None
            if isinstance(info, dict):
                return info.get("collection") or info.get("collection_name")
            return getattr(info, "collection", None)

        return await run_in_threadpool(_describe)


def _alias_exists(client, alias: str) -> bool:
    try:
        client.describe_alias(alias)
        return True
    except Exception:  # noqa: BLE001
        return False


_vector_index: VectorIndex | None = None


def get_vector_index() -> VectorIndex:
    global _vector_index
    if _vector_index is None:
        _vector_index = MilvusVectorIndex() if settings.milvus_uri else MemoryVectorIndex()
    return _vector_index
