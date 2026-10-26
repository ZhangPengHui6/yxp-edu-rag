"""LangGraph state for the offline ingestion pipeline.

``IngestionState`` is the data that flows between nodes. ``IngestionRuntime``
holds the objects that must not be serialized (the async session, the ORM job,
the step rows and the OCR provider); the graph runs without a checkpointer, so
keeping them in state is safe and avoids re-querying for every node.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.providers import OcrDocument, OcrProvider
from app.models import IngestionJob, IngestionJobStep
from app.modules.ingestion.chunker import Chunk
from app.modules.ingestion.extract import ExtractedQuestion
from app.modules.ingestion.llm_extract import LlmExtraction


@dataclass
class IngestionRuntime:
    """Non-serializable collaborators shared by every node."""

    session: AsyncSession
    job: IngestionJob
    steps: list[IngestionJobStep]
    provider: OcrProvider | None = None


class IngestionState(TypedDict, total=False):
    # identity / control
    job_id: str
    mode: str  # "real" | "simulated"
    runtime: IngestionRuntime
    canceled: bool

    # upload
    content: bytes
    filename: str

    # ocr
    document: OcrDocument
    ocr_name: str
    pages: int

    # split
    chunks: list[Chunk]
    average_chunk_chars: int

    # struct
    knowledge_points: list[dict[str, Any]]
    chunk_docs: list[dict[str, Any]]
    question_docs: list[dict[str, Any]]
    extracted: list[ExtractedQuestion]
    llm_extraction: LlmExtraction | None
    metrics: dict[str, Any]

    # quality / final output
    stats: dict[str, Any]
