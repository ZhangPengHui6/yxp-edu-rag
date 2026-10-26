"""Provider contracts for the data pipeline.

The real ingestion pipeline uses MinerU (or Qwen-VL as a fallback) for OCR,
``qwen-flash`` for question/knowledge-point structuring, a reranker for evidence
scoring and BGE-M3 for dense + sparse embeddings. Vector collections are built
by ``app.modules.publish.indexer`` during the publish stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import settings
from app.core.errors import ProviderUnavailable


@dataclass(slots=True)
class OcrBlock:
    index: int
    type: str  # 标题 / 正文 / 公式 / 表格 / 图注
    content: str
    confidence: float
    page: int


@dataclass(slots=True)
class OcrDocument:
    markdown: str
    page_count: int
    blocks: list[OcrBlock] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LlmMessage:
    role: str
    content: str


class OcrProvider(Protocol):
    name: str

    async def parse(self, *, content: bytes, filename: str) -> OcrDocument:
        ...


class LlmProvider(Protocol):
    name: str

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.1,
        response_format: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        ...


@dataclass(slots=True)
class EmbeddingBatch:
    """Dense vectors plus BGE-M3 sparse lexical weights, one entry per text."""

    dense: list[list[float]] = field(default_factory=list)
    sparse: list[dict[int, float]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.dense)


class EmbeddingProvider(Protocol):
    name: str
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    async def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        ...


class RerankProvider(Protocol):
    name: str

    async def rerank(self, query: str, documents: list[str], *, top_n: int = 5) -> list[int]:
        ...

    async def rerank_with_scores(
        self, query: str, documents: list[str], *, top_n: int = 5
    ) -> list[tuple[int, float]]:
        ...


class UnavailableOcrProvider:
    name = "unconfigured"

    async def parse(self, *, content: bytes, filename: str) -> OcrDocument:
        provider = settings.ocr_provider.strip().lower()
        if provider == "mineru":
            message = "MINERU_TOKEN 未配置，无法调用 MinerU 解析"
        else:
            message = f"OCR Provider {provider or '(空)'} 尚未接入"
        raise ProviderUnavailable(
            message,
            details={"provider": provider, "filename": filename},
        )


class UnavailableLlmProvider:
    name = "unconfigured"

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.1,
        response_format: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        raise ProviderUnavailable("LLM Provider 尚未接入；请配置 QWEN/DASHSCOPE_API_KEY")


class UnavailableEmbeddingProvider:
    name = "unconfigured"
    dimension = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise ProviderUnavailable(
            "Embedding Provider 未启用；如需向量化请设置 EMBEDDING_PROVIDER=bge_m3"
        )

    async def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        raise ProviderUnavailable(
            "Embedding Provider 未启用；如需向量化请设置 EMBEDDING_PROVIDER=bge_m3"
        )


class UnavailableRerankProvider:
    name = "unconfigured"

    async def rerank(self, query: str, documents: list[str], *, top_n: int = 5) -> list[int]:
        raise ProviderUnavailable("Rerank Provider 尚未配置")

    async def rerank_with_scores(
        self, query: str, documents: list[str], *, top_n: int = 5
    ) -> list[tuple[int, float]]:
        raise ProviderUnavailable("Rerank Provider 尚未配置")


def _mineru_provider() -> OcrProvider | None:
    has_auth = bool(
        settings.mineru_token.strip()
        or (settings.mineru_access_key_id.strip() and settings.mineru_secret_access_key.strip())
    )
    if not has_auth:
        return None
    from app.infra.providers.mineru import MineruOcrProvider

    return MineruOcrProvider()


def _qwen_vl_provider() -> OcrProvider | None:
    if not settings.dashscope_api_key.strip():
        return None
    from app.infra.providers.qwen_vl import QwenVlOcrProvider

    return QwenVlOcrProvider()


def get_ocr_provider() -> OcrProvider:
    """Return the configured OCR provider.

    ``auto`` prefers MinerU (better layout/formula fidelity) and falls back to
    page-by-page Qwen-VL when the MinerU token is missing or rejected.
    """
    provider = settings.ocr_provider.strip().lower()
    if provider == "auto":
        return _mineru_provider() or _qwen_vl_provider() or UnavailableOcrProvider()
    if provider == "mineru":
        return _mineru_provider() or UnavailableOcrProvider()
    if provider == "qwen_vl":
        return _qwen_vl_provider() or UnavailableOcrProvider()
    return UnavailableOcrProvider()


def get_ocr_fallback(primary_name: str) -> OcrProvider | None:
    """Return the other OCR provider for a one-shot fallback, if configured."""
    if primary_name == "mineru":
        return _qwen_vl_provider()
    if primary_name == "qwen_vl":
        return _mineru_provider()
    return None


def get_llm_provider() -> LlmProvider:
    if not settings.dashscope_api_key.strip():
        return UnavailableLlmProvider()
    from app.infra.providers.qwen import QwenLlmProvider

    return QwenLlmProvider()


def get_embedding_provider() -> EmbeddingProvider:
    """Return the configured embedding provider.

    ``bge_m3`` loads the local BGE-M3 snapshot (``BGE_M3_PATH`` or ``BGE_M3``)
    and returns 1024-d dense vectors plus sparse lexical weights. ``off`` (the
    default) skips vectorization, so ingestion keeps writing text-only documents.
    """
    provider = settings.embedding_provider.strip().lower()
    if provider in {"bge_m3", "bge", "local", "auto"}:
        from app.infra.providers.bge import BgeM3EmbeddingProvider

        return BgeM3EmbeddingProvider()
    return UnavailableEmbeddingProvider()


def _dashscope_rerank() -> RerankProvider:
    if not settings.rerank_api_key.strip() or not settings.rerank_base_url.strip():
        return UnavailableRerankProvider()
    from app.infra.providers.qwen import QwenRerankProvider

    return QwenRerankProvider()


def _qwen_llm_rerank() -> RerankProvider:
    if not settings.dashscope_api_key.strip():
        return UnavailableRerankProvider()
    from app.infra.providers.qwen import QwenLlmRerankProvider

    return QwenLlmRerankProvider()


def get_rerank_provider() -> RerankProvider:
    """Return the configured reranker.

    ``qwen_llm`` scores candidates with the chat model (works on every
    OpenAI-compatible gateway, including Qiniu, which has no dedicated rerank
    endpoint); ``dashscope`` calls the DashScope text-rerank service. ``auto``
    prefers the chat model and keeps DashScope as a secondary option.
    """
    provider = settings.rerank_provider.strip().lower()
    if provider in {"off", "none", "disabled", "placeholder"}:
        return UnavailableRerankProvider()
    if provider == "dashscope":
        return _dashscope_rerank()
    if provider in {"qwen_llm", "llm"}:
        return _qwen_llm_rerank()
    llm_reranker = _qwen_llm_rerank()
    if llm_reranker.name != "unconfigured":
        return llm_reranker
    return _dashscope_rerank()
