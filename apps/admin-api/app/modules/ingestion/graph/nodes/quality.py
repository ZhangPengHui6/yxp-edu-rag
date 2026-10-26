"""Node 5: quality metrics and evidence reranking."""

from __future__ import annotations

from app.modules.ingestion.evidence import rerank_evidence
from app.modules.ingestion.graph import deps
from app.modules.ingestion.graph.base import IngestionNode, NodeResult
from app.modules.ingestion.graph.persistence import apply_question_evidence
from app.modules.ingestion.graph.state import IngestionState
from app.shared.time import utcnow


class QualityNode(IngestionNode):
    key = "quality"
    position = 4
    simulated_detail = "质量检查通过（模拟流水线）"

    async def process(self, state: IngestionState) -> NodeResult:
        chunks = state["chunks"]
        chunk_docs = state["chunk_docs"]
        question_docs = state["question_docs"]
        knowledge_points = state["knowledge_points"]
        metrics = state.get("metrics") or {}

        answered = sum(1 for doc in question_docs if str(doc.get("answer", "")).strip())
        answer_rate = round(answered / len(question_docs) * 100) if question_docs else 0
        long_chunks = sum(1 for chunk in chunks if chunk.char_count >= 80)
        chunk_rate = round(long_chunks / len(chunks) * 100) if chunks else 0

        reranker = deps.get_rerank_provider()
        evidence = await rerank_evidence(state["extracted"], chunk_docs, reranker)
        reranked = await apply_question_evidence(question_docs, evidence)

        stats = {
            "pipeline": "real",
            "ocrProvider": state["ocr_name"],
            "pages": state["pages"],
            "chunks": len(chunk_docs),
            "questions": len(question_docs),
            "knowledgePoints": len(knowledge_points),
            "averageChunkChars": state["average_chunk_chars"],
            "answeredQuestions": answered,
            "llmStructuring": bool(metrics.get("llmUsed", False)),
            "llmQuestions": int(metrics.get("llmQuestions", 0)),
            "llmKnowledgePoints": int(metrics.get("llmKnowledgePoints", 0)),
            "rerankedQuestions": reranked,
            "rerankProvider": reranker.name if reranked else "",
            "embeddedChunks": int(metrics.get("embeddedChunks", 0)),
            "embeddedQuestions": int(metrics.get("embeddedQuestions", 0)),
            "embeddingProvider": str(metrics.get("embeddingProvider", "")),
            "finishedAt": utcnow().isoformat(timespec="seconds"),
        }
        return NodeResult(
            update={"stats": stats},
            detail=(
                f"质量检查完成：有效片段 {chunk_rate}%，题目答案覆盖 {answer_rate}%，"
                f"证据匹配 {reranked} 道"
            ),
        )
