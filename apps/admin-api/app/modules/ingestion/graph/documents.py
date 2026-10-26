"""MongoDB document builders and vector attachment.

Pure transformations live here so the ``struct`` node stays close to its
orchestration logic and can be unit-tested without Mongo or providers.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from app.core.errors import ProviderUnavailable
from app.infra.providers import EmbeddingProvider
from app.infra.vectors import sparse_to_document
from app.models import IngestionJob
from app.modules.ingestion.chunker import Chunk
from app.modules.ingestion.extract import (
    ExtractedQuestion,
    find_kp_id,
    heading_names,
    normalize_kp_name,
    question_document,
)


def chunk_id(tenant_id: str, job_id: str, content_hash: str) -> str:
    digest = hashlib.sha1(f"{tenant_id}|{job_id}|{content_hash}".encode()).hexdigest()
    return f"chk_{digest[:20]}"


def chunk_document(
    chunk: Chunk,
    *,
    job: IngestionJob,
    tenant_id: str,
    subject: str,
    stage: str,
    root_name: str,
    book_title: str,
    by_path: dict[str, str],
    now: datetime,
) -> dict[str, Any]:
    location = (
        f"第 {chunk.page_start} 页"
        if chunk.page_start == chunk.page_end
        else f"第 {chunk.page_start}-{chunk.page_end} 页"
    )
    names = heading_names(chunk.heading_path, root_name=root_name, book_title=book_title)
    path = " / ".join([root_name, *names])
    node_id = by_path.get(path)
    return {
        "_id": chunk_id(tenant_id, job.job_id, chunk.hash),
        "tenant_id": tenant_id,
        "job_id": job.job_id,
        "book_id": f"book_{job.job_id}",
        "book_title": book_title,
        "edition": "",
        "subject": subject,
        "stage": stage,
        "grade": "",
        "location": location,
        "page": chunk.page_start,
        "page_end": chunk.page_end,
        "content": chunk.content,
        "evidence": chunk.content,
        "knowledge_point_ids": [node_id] if node_id else [],
        "heading_path": chunk.heading_path,
        "content_type": chunk.content_type,
        "char_count": chunk.char_count,
        "token_count": chunk.token_count,
        "hash": chunk.hash,
        "status": "indexed",
        "created_at": now,
    }


def build_question_document(
    question: ExtractedQuestion,
    *,
    tenant_id: str,
    job_id: str,
    subject: str,
    stage: str,
    filename: str,
    by_name: dict[str, str],
) -> dict[str, Any]:
    ids: list[str] = []
    for name in question.kp_names:
        node_id = find_kp_id(normalize_kp_name(name), by_name)
        if node_id and node_id not in ids:
            ids.append(node_id)
    return question_document(
        question,
        tenant_id=tenant_id,
        job_id=job_id,
        subject=subject,
        stage=stage,
        filename=filename,
        kp_ids=ids,
    )


async def embed_assets(
    embedder: EmbeddingProvider,
    chunk_docs: list[dict[str, Any]],
    question_docs: list[dict[str, Any]],
) -> tuple[int, int]:
    """Attach BGE-M3 dense/sparse vectors to chunk and question documents.

    Dense vectors feed cosine search; the sparse weights on the stem feed the
    lexical index. MongoDB keys must be strings, so sparse weights are stored as
    ``{token_id: weight}`` and converted back to ints when written to Milvus.
    """
    if chunk_docs:
        batch = await embedder.embed_documents([str(doc["content"]) for doc in chunk_docs])
        if len(batch) != len(chunk_docs):
            raise ProviderUnavailable(
                f"{embedder.name} 返回 {len(batch)} 个片段向量，期望 {len(chunk_docs)}"
            )
        for doc, dense, sparse in zip(chunk_docs, batch.dense, batch.sparse, strict=True):
            doc["dense_vector"] = [float(value) for value in dense]
            doc["sparse_vector"] = sparse_to_document(sparse)
            doc["embedding_model"] = embedder.name

    if question_docs:
        stem_batch = await embedder.embed_documents([str(doc["stem"]) for doc in question_docs])
        if len(stem_batch) != len(question_docs):
            raise ProviderUnavailable(
                f"{embedder.name} 返回 {len(stem_batch)} 个题干向量，"
                f"期望 {len(question_docs)}"
            )
        for doc, dense, sparse in zip(
            question_docs, stem_batch.dense, stem_batch.sparse, strict=True
        ):
            doc["stem_vector"] = [float(value) for value in dense]
            doc["sparse_vector"] = sparse_to_document(sparse)
            doc["embedding_model"] = embedder.name

        solution_texts = [
            str(doc.get("analysis") or doc.get("answer") or "") for doc in question_docs
        ]
        indexes = [index for index, text in enumerate(solution_texts) if text.strip()]
        if indexes:
            solution_batch = await embedder.embed_documents(
                [solution_texts[index] for index in indexes]
            )
            if len(solution_batch) != len(indexes):
                raise ProviderUnavailable(
                    f"{embedder.name} 返回 {len(solution_batch)} 个解答向量，"
                    f"期望 {len(indexes)}"
                )
            for position, index in enumerate(indexes):
                question_docs[index]["solution_vector"] = [
                    float(value) for value in solution_batch.dense[position]
                ]
        for doc in question_docs:
            doc.setdefault("solution_vector", [0.0] * embedder.dimension)

    return len(chunk_docs), len(question_docs)
