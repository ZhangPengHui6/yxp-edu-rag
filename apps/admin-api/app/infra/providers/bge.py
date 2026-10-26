"""Local BGE-M3 embedding provider.

``BAAI/bge-m3`` produces 1024-d dense vectors (used with cosine distance) and
sparse lexical weights (used with inner product) in a single forward pass, which
is exactly the shape of the Milvus collections declared in the data model.

The model is heavy (~2.2 GB), so it is loaded lazily on first use and inference
runs in the thread pool to keep the event loop responsive.
"""

from __future__ import annotations

import threading
from typing import Any

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.infra.providers import EmbeddingBatch

log = get_logger("bge_m3")


class BgeM3EmbeddingProvider:
    name = "bge-m3"
    dimension = 1024

    def __init__(self) -> None:
        self.model_path = settings.bge_m3_path.strip() or settings.bge_m3.strip()
        self.device = settings.bge_device.strip() or "cpu"
        # Half precision is only meaningful on CUDA; CPU kernels are fp32.
        self.use_fp16 = bool(settings.bge_fp16) and self.device.lower().startswith("cuda")
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        try:
            from FlagEmbedding import BGEM3FlagModel
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ProviderUnavailable(
                "本地 BGE-M3 需要 FlagEmbedding 与 torch，请先执行 `uv sync --extra embeddings`",
                details={"model": self.model_path},
            ) from exc
        if not self.model_path:
            raise ProviderUnavailable("未配置 BGE_M3_PATH / BGE_M3，无法加载 BGE-M3")
        try:
            model = BGEM3FlagModel(
                self.model_path,
                use_fp16=self.use_fp16,
                device=self.device,
            )
        except Exception as exc:  # noqa: BLE001 - transformers raises many types
            raise ProviderUnavailable(
                f"BGE-M3 模型加载失败（{self.model_path}）：{exc}",
                details={"model": self.model_path, "device": self.device},
            ) from exc
        log.info(
            "bge_m3_loaded",
            model=self.model_path,
            device=self.device,
            fp16=self.use_fp16,
        )
        return model

    def _encode(self, texts: list[str]) -> EmbeddingBatch:
        with self._lock:
            if self._model is None:
                self._model = self._load()
            try:
                result = self._model.encode(
                    texts,
                    batch_size=max(1, settings.embedding_batch_size),
                    max_length=max(64, settings.embedding_max_length),
                    return_dense=True,
                    return_sparse=True,
                    return_colbert_vecs=False,
                )
            except Exception as exc:  # noqa: BLE001
                raise ProviderUnavailable(f"BGE-M3 向量化失败：{exc}") from exc

        dense = [[float(value) for value in row] for row in result.get("dense_vecs", [])]
        sparse = [
            {int(key): float(value) for key, value in (weights or {}).items()}
            for weights in result.get("lexical_weights", [])
        ]
        if len(dense) != len(texts):
            raise ProviderUnavailable(
                f"BGE-M3 返回 {len(dense)} 条向量，输入为 {len(texts)} 条"
            )
        if len(sparse) < len(texts):
            sparse.extend({} for _ in range(len(texts) - len(sparse)))
        return EmbeddingBatch(dense=dense, sparse=sparse)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return (await self.embed_documents(texts)).dense

    async def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch()
        from starlette.concurrency import run_in_threadpool

        return await run_in_threadpool(self._encode, list(texts))
