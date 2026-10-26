"""Node 4: knowledge points, questions, chunks and vectors (LLM + rules)."""

from __future__ import annotations

from pathlib import PurePath
from typing import Any

from app.core.config import settings
from app.modules.ingestion.extract import (
    STAGE_PREFIX,
    apply_llm_knowledge_points,
    collect_knowledge_points,
    extract_questions,
    merge_extracted_questions,
    normalize_kp_name,
)
from app.modules.ingestion.graph import deps
from app.modules.ingestion.graph.base import IngestionNode, NodeResult
from app.modules.ingestion.graph.documents import (
    build_question_document,
    chunk_document,
    embed_assets,
)
from app.modules.ingestion.graph.persistence import (
    clear_job_assets,
    load_existing_knowledge_points,
    refresh_knowledge_counts,
    write_chunks,
    write_knowledge_points,
    write_questions,
)
from app.modules.ingestion.graph.state import IngestionState
from app.modules.ingestion.llm_extract import LlmExtraction, extract_with_llm
from app.shared.time import utcnow


class StructNode(IngestionNode):
    key = "struct"
    position = 3
    simulated_detail = "结构化与公式规整完成（模拟流水线）"

    async def process(self, state: IngestionState) -> NodeResult:
        job = state["runtime"].job
        tenant_id = job.tenant_id
        subject = job.subject.strip() or "未分类"
        stage = job.stage or "middle"
        filename = state["filename"]
        book_title = PurePath(filename).stem
        root_name = f"{STAGE_PREFIX.get(stage, '')}{subject}".strip() or "未分类"
        chunks = state["chunks"]
        markdown = state["document"].markdown
        now = utcnow()

        existing = await load_existing_knowledge_points(tenant_id, subject, stage)
        knowledge_points = collect_knowledge_points(
            chunks,
            tenant_id=tenant_id,
            subject=subject,
            stage=stage,
            book_title=book_title,
            existing_ids=existing,
        )

        llm_extraction: LlmExtraction | None = None
        llm_knowledge_points = 0
        if settings.llm_structuring:
            llm_extraction = await extract_with_llm(
                deps.get_llm_provider(),
                chunks,
                subject=subject,
                stage=stage,
                filename=filename,
            )
        if llm_extraction is not None:
            knowledge_points, llm_knowledge_points = apply_llm_knowledge_points(
                knowledge_points,
                llm_extraction.knowledge_points,
                tenant_id=tenant_id,
                subject=subject,
                stage=stage,
                root_name=root_name,
            )

        by_path = {item["path"]: item["_id"] for item in knowledge_points}
        by_name: dict[str, str] = {}
        for item in knowledge_points:
            if item["level"] > 0:
                by_name.setdefault(normalize_kp_name(item["name"]), item["_id"])

        chunk_docs = [
            chunk_document(
                chunk,
                job=job,
                tenant_id=tenant_id,
                subject=subject,
                stage=stage,
                root_name=root_name,
                book_title=book_title,
                by_path=by_path,
                now=now,
            )
            for chunk in chunks
        ]
        rule_questions = extract_questions(markdown, subject=subject, filename=filename)
        extracted = (
            merge_extracted_questions(llm_extraction.questions, rule_questions)
            if llm_extraction is not None and llm_extraction.questions
            else rule_questions
        )
        question_docs = [
            build_question_document(
                question,
                tenant_id=tenant_id,
                job_id=job.job_id,
                subject=subject,
                stage=stage,
                filename=filename,
                by_name=by_name,
            )
            for question in extracted
        ]

        embedder = deps.get_embedding_provider()
        embedded_chunks = 0
        embedded_questions = 0
        if embedder.name != "unconfigured":
            embedded_chunks, embedded_questions = await embed_assets(
                embedder, chunk_docs, question_docs
            )

        await clear_job_assets(tenant_id, job.job_id)
        await write_knowledge_points(knowledge_points, now)
        await write_chunks(chunk_docs)
        await write_questions(question_docs)
        await refresh_knowledge_counts(
            tenant_id, [item["_id"] for item in knowledge_points]
        )

        llm_questions = len(llm_extraction.questions) if llm_extraction else 0
        structuring = (
            f"Qwen 结构化 {llm_questions} 道题 / 新增 {llm_knowledge_points} 个知识点"
            if llm_extraction is not None
            else "规则抽取（LLM 未启用或调用失败）"
        )
        vectorizing = (
            f" · {embedder.name} 向量化 {embedded_chunks} 个片段 / "
            f"{embedded_questions} 道题目"
            if embedder.name != "unconfigured"
            else " · 未向量化（EMBEDDING_PROVIDER=off）"
        )

        metrics: dict[str, Any] = {
            "llmUsed": llm_extraction is not None,
            "llmQuestions": llm_questions,
            "llmKnowledgePoints": llm_knowledge_points,
            "embeddedChunks": embedded_chunks,
            "embeddedQuestions": embedded_questions,
            "embeddingProvider": embedder.name if embedder.name != "unconfigured" else "",
        }
        return NodeResult(
            update={
                "knowledge_points": knowledge_points,
                "chunk_docs": chunk_docs,
                "question_docs": question_docs,
                "extracted": extracted,
                "llm_extraction": llm_extraction,
                "metrics": metrics,
            },
            detail=(
                f"结构化完成：{len(knowledge_points)} 个知识点 · "
                f"{len(chunk_docs)} 个片段 · {len(question_docs)} 道题目"
                f"（{structuring}）{vectorizing}"
            ),
        )
