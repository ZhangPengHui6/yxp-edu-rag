"""Helpers shared by ingestion and the publish index builder.

Vectors live in two representations:

* BGE-M3 / Milvus: dense ``list[float]`` and sparse ``dict[int, float]``;
* MongoDB: BSON requires string keys, so sparse weights become ``dict[str, float]``.
"""

from __future__ import annotations

from typing import Any


def sparse_to_document(sparse: dict[int, float]) -> dict[str, float]:
    """Convert sparse weights to the MongoDB representation."""
    return {
        str(key): round(float(value), 6)
        for key, value in sparse.items()
        if value > 0
    }


def sparse_to_field(value: Any) -> dict[int, float]:
    """Convert a stored sparse weight map to Milvus' ``dict[int, float]``."""
    if not isinstance(value, dict):
        return {0: 0.0}
    weights: dict[int, float] = {}
    for key, weight in value.items():
        try:
            weights[int(key)] = float(weight)
        except (TypeError, ValueError):
            continue
    return weights or {0: 0.0}


def dense_to_field(value: Any, dimension: int) -> list[float]:
    """Normalize a stored dense vector, raising ``ValueError`` when it is unusable."""
    if not isinstance(value, list) or len(value) != dimension:
        raise ValueError(f"向量维度应为 {dimension}")
    return [float(item) for item in value]


def has_dense(value: Any, dimension: int) -> bool:
    return isinstance(value, list) and len(value) == dimension
