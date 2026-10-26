"""Unit tests for the real ingestion building blocks and an end-to-end write."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from httpx import AsyncClient

from app.infra import mongo
from app.infra.chunk_store import get_chunk_store
from app.infra.pg import SessionLocal
from app.infra.providers import EmbeddingBatch, LlmMessage, OcrDocument
from app.models import IngestionJob
from app.modules.ingestion import pipeline
from app.modules.ingestion.chunker import build_chunks
from app.modules.ingestion.extract import (
    apply_llm_knowledge_points,
    collect_knowledge_points,
    extract_questions,
)
from app.modules.ingestion.graph import deps
from app.modules.ingestion.llm_extract import extract_with_llm

SAMPLE = """<!-- page: 1 -->
# 高中数学知识手册

## 第一章 函数

### 1.1 函数的性质

函数是描述变量之间依赖关系的数学对象，主要研究定义域、值域以及单调性等性质。例如 y=x² 在区间 [0,+∞) 上单调递增。

<!-- page: 2 -->

例1. 已知函数 f(x)=x²，求 f(2) 的值。
A. 2
B. 4
C. 6
D. 8
答案：B
解析：将 x=2 代入函数解析式可得 f(2)=4，因此选择 B。
"""


def test_chunks_keep_pages_and_heading_path() -> None:
    chunks = build_chunks(SAMPLE)
    assert chunks
    assert chunks[0].heading_path.startswith("高中数学知识手册 / 第一章 函数")
    assert chunks[0].page_start == 1
    assert chunks[0].content_type == "text"


def test_extract_question_with_options_and_answer() -> None:
    questions = extract_questions(SAMPLE, subject="数学", filename="高中数学知识手册.pdf")
    assert len(questions) == 1
    question = questions[0]
    assert question.qtype == "single_choice"
    assert [option["key"] for option in question.options] == ["A", "B", "C", "D"]
    assert question.answer == "B"
    assert "f(2)=4" in question.analysis
    assert question.page == 2


def test_knowledge_points_skip_book_title_and_merge_existing() -> None:
    chunks = build_chunks(SAMPLE)
    nodes = collect_knowledge_points(
        chunks,
        tenant_id="t1",
        subject="数学",
        stage="high",
        book_title="高中数学知识手册",
    )
    paths = {node["path"] for node in nodes}
    assert "高中数学" in paths
    assert "高中数学 / 第一章 函数 / 1.1 函数的性质" in paths
    assert "高中数学知识手册" not in paths

    existing = {(None, "高中数学"): "kp_m_root"}
    merged = collect_knowledge_points(
        chunks,
        tenant_id="t1",
        subject="数学",
        stage="high",
        book_title="高中数学知识手册",
        existing_ids=existing,
    )
    assert next(node for node in merged if node["path"] == "高中数学")["_id"] == "kp_m_root"


class _FakeStorage:
    async def get_bytes(self, key: str) -> bytes:
        return b"%PDF-1.4 fake"


class _FakeProvider:
    name = "fake-ocr"

    async def parse(self, *, content: bytes, filename: str) -> OcrDocument:
        return OcrDocument(markdown=SAMPLE, page_count=2, blocks=[])


@pytest.mark.usefixtures("client")
async def test_real_pipeline_writes_chunks_questions_and_knowledge_points(
    client: AsyncClient,
    editor: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "use_real_pipeline", lambda: True)
    monkeypatch.setattr(deps, "get_ocr_provider", lambda: _FakeProvider())
    monkeypatch.setattr(deps, "get_storage", lambda: _FakeStorage())

    created = await client.post(
        "/admin/v1/ingestion/jobs",
        headers=editor,
        json={
            "filename": "高中数学知识手册.pdf",
            "objectKey": "uploads/2026/10/book.pdf",
            "sourceType": "PDF",
            "subject": "数学",
            "stage": "high",
            "pages": 2,
            "sizeMb": 1.0,
        },
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["data"]["id"]

    final: dict = {}
    for _ in range(100):
        response = await client.get(f"/admin/v1/ingestion/jobs/{job_id}", headers=editor)
        final = response.json()["data"]
        if final["status"] in {"succeeded", "failed"}:
            break
        await asyncio.sleep(0.05)

    assert final["status"] == "succeeded", final
    assert await get_chunk_store().count_by_job("tnt_yxp", job_id) == 1
    questions = await mongo.col("questions").find({"job_id": job_id}).to_list(10)
    assert len(questions) == 1
    assert questions[0]["answer"] == "B"
    points = await mongo.col("knowledge_points").find({"name": "1.1 函数的性质"}).to_list(10)
    assert len(points) == 1
    assert questions[0]["knowledge_point_ids"] == [points[0]["_id"]]


LLM_PAYLOAD: dict[str, Any] = {
    "knowledge_points": [
        {"name": "函数的单调性", "parent": "1.1 函数的性质", "description": "研究函数增减"},
        {"name": "函数定义域", "parent": None, "description": "自变量取值范围"},
    ],
    "questions": [
        {
            "chunk": 1,
            "page": 2,
            "type": "single_choice",
            "stem": "已知函数 f(x)=x²，求 f(3) 的值。",
            "options": [{"key": "A", "text": "3"}, {"key": "B", "text": "9"}],
            "answer": "B",
            "analysis": "将 x=3 代入可得 9。",
            "difficulty": "easy",
            "knowledge_points": ["函数的单调性"],
        }
    ],
}


class _FakeLlm:
    name = "fake-llm"

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or LLM_PAYLOAD
        self.calls = 0

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.1,
        response_format: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False)


class _FakeEmbedding:
    name = "fake-bge"
    dimension = 4

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return (await self.embed_documents(texts)).dense

    async def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        return EmbeddingBatch(
            dense=[[0.1, 0.2, 0.3, 0.4] for _ in texts],
            sparse=[{10: 0.5, 11: 0.25} for _ in texts],
        )


class _FakeReranker:
    name = "fake-rerank"

    async def rerank_with_scores(
        self, query: str, documents: list[str], *, top_n: int = 5
    ) -> list[tuple[int, float]]:
        return [(0, 0.91)][:top_n]

    async def rerank(self, query: str, documents: list[str], *, top_n: int = 5) -> list[int]:
        return [0][:top_n]


async def test_llm_structuring_builds_questions_and_knowledge_points() -> None:
    chunks = build_chunks(SAMPLE)
    extraction = await extract_with_llm(
        _FakeLlm(),
        chunks,
        subject="数学",
        stage="high",
        filename="高中数学知识手册.pdf",
    )
    assert extraction is not None
    assert len(extraction.questions) == 1
    question = extraction.questions[0]
    assert question.origin == "llm"
    assert question.qtype == "single_choice"
    assert question.page == 2
    assert question.difficulty == "easy"
    assert question.kp_names == ["函数的单调性"]

    nodes = collect_knowledge_points(
        chunks,
        tenant_id="t1",
        subject="数学",
        stage="high",
        book_title="高中数学知识手册",
    )
    nodes, added = apply_llm_knowledge_points(
        nodes,
        extraction.knowledge_points,
        tenant_id="t1",
        subject="数学",
        stage="high",
        root_name="高中数学",
    )
    assert added == 2
    paths = {node["path"] for node in nodes}
    assert "高中数学 / 第一章 函数 / 1.1 函数的性质 / 函数的单调性" in paths
    assert "高中数学 / 函数定义域" in paths

    # 模型常省略编号：不新建节点，只补描述。
    nodes, added_again = apply_llm_knowledge_points(
        nodes,
        [("函数的性质", None, "LLM 补充说明")],
        tenant_id="t1",
        subject="数学",
        stage="high",
        root_name="高中数学",
    )
    assert added_again == 0
    heading = next(node for node in nodes if node["name"] == "1.1 函数的性质")
    assert heading["description"] == "LLM 补充说明"


@pytest.mark.usefixtures("client")
async def test_real_pipeline_uses_llm_structuring_and_rerank(
    client: AsyncClient,
    editor: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "use_real_pipeline", lambda: True)
    monkeypatch.setattr(deps, "get_ocr_provider", lambda: _FakeProvider())
    monkeypatch.setattr(deps, "get_storage", lambda: _FakeStorage())
    monkeypatch.setattr(deps, "get_llm_provider", lambda: _FakeLlm())
    monkeypatch.setattr(deps, "get_rerank_provider", lambda: _FakeReranker())
    monkeypatch.setattr(deps, "get_embedding_provider", lambda: _FakeEmbedding())

    created = await client.post(
        "/admin/v1/ingestion/jobs",
        headers=editor,
        json={
            "filename": "高中数学知识手册.pdf",
            "objectKey": "uploads/2026/10/book.pdf",
            "sourceType": "PDF",
            "subject": "数学",
            "stage": "high",
            "pages": 2,
            "sizeMb": 1.0,
        },
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["data"]["id"]

    final: dict = {}
    for _ in range(100):
        response = await client.get(f"/admin/v1/ingestion/jobs/{job_id}", headers=editor)
        final = response.json()["data"]
        if final["status"] in {"succeeded", "failed"}:
            break
        await asyncio.sleep(0.05)

    assert final["status"] == "succeeded", final
    async with SessionLocal() as session:
        job = await session.get(IngestionJob, job_id)
        assert job is not None
        stats = dict(job.stats or {})
    assert stats["llmStructuring"] is True
    assert stats["llmQuestions"] == 1
    assert stats["llmKnowledgePoints"] == 2
    assert stats["rerankedQuestions"] == 2
    assert stats["embeddedChunks"] == 1
    assert stats["embeddedQuestions"] == 2
    assert stats["embeddingProvider"] == "fake-bge"

    chunks = [
        doc
        for doc in await get_chunk_store().all("tnt_yxp")
        if doc.get("job_id") == job_id
    ]
    assert chunks[0]["subject"] == "数学"
    assert chunks[0]["stage"] == "high"
    assert chunks[0]["dense_vector"] == [0.1, 0.2, 0.3, 0.4]
    assert chunks[0]["sparse_vector"] == {"10": 0.5, "11": 0.25}

    questions = await mongo.col("questions").find({"job_id": job_id}).to_list(10)
    assert len(questions) == 2
    llm_question = next(question for question in questions if "f(3)" in question["stem"])
    assert llm_question["difficulty"] == "easy"
    assert llm_question["knowledge_point_ids"]
    assert llm_question["evidence_chunk_ids"]
    assert llm_question["evidence_score"] == 0.91
    assert llm_question["stem_vector"] == [0.1, 0.2, 0.3, 0.4]
    assert llm_question["solution_vector"] == [0.1, 0.2, 0.3, 0.4]

    points = await mongo.col("knowledge_points").find({"name": "函数的单调性"}).to_list(10)
    assert len(points) == 1
    assert points[0]["parent_id"]
