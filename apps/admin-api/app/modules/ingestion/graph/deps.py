"""Provider/storage lookups used by graph nodes.

Nodes resolve their collaborators through this module instead of importing the
factories directly, so tests can replace one symbol and exercise the graph
without touching the real infrastructure.
"""

from __future__ import annotations

from app.infra.minio import get_storage
from app.infra.providers import (
    get_embedding_provider,
    get_llm_provider,
    get_ocr_fallback,
    get_ocr_provider,
    get_rerank_provider,
)

__all__ = [
    "get_embedding_provider",
    "get_llm_provider",
    "get_ocr_fallback",
    "get_ocr_provider",
    "get_rerank_provider",
    "get_storage",
]
