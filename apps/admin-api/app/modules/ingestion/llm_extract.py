"""LLM structuring: turn OCR Markdown chunks into questions and knowledge points.

``qwen-flash`` (Qiniu's OpenAI-compatible gateway by default) works on batches of
chunks and returns JSON. The deterministic heading tree / marker parser in
``extract.py`` stays as a fallback: a failed batch is skipped with a warning, so
a flaky model degrades recall instead of failing the ingestion job.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.infra.providers import LlmMessage, LlmProvider
from app.modules.ingestion.chunker import Chunk
from app.modules.ingestion.extract import ExtractedQuestion, normalize_kp_name, stem_key

log = get_logger("llm_extract")

SYSTEM_PROMPT = """你是 K12 教材结构化专家。输入是教材/知识手册按页转写后的 Markdown 片段，
请抽取知识点和题目，严格输出 JSON 对象：

{
  "knowledge_points": [
    {"name": "知识点名称", "parent": "上级标题或已有知识点名称，没有则 null", "description": "一句话说明"}
  ],
  "questions": [
    {
      "chunk": 片段编号,
      "page": 页码,
      "type": "single_choice|fill_blank|calculation",
      "stem": "完整题干，不含题号",
      "options": [{"key": "A", "text": "选项内容"}],
      "answer": "答案，原文没有则空字符串",
      "analysis": "解析，原文没有则空字符串",
      "difficulty": "easy|medium|hard",
      "knowledge_points": ["该题考查的知识点名称"]
    }
  ]
}

规则：
1. 只抽取片段中真实出现的内容，不得编造题目、答案或解析；
2. 题目必须完整（题干 + 选项/填空），残缺的不要输出；
3. 选择题选项放入 options，其他题型 options 为空数组；
4. 除选择题外，题型用 fill_blank（填空）或 calculation（解答/证明/计算）；
5. knowledge_points 的名称尽量与片段标题、知识点名称一致，便于合并；
6. page 使用片段标注的页码；无法确定时用片段起始页；
7. 没有可抽取内容时返回 {"knowledge_points": [], "questions": []}。"""

REPAIR_PROMPT = """上一条输出不是合法 JSON。请只重新输出符合 schema 的 JSON 对象，
不要输出解释、前后缀或 Markdown 代码围栏。"""

_FENCE_RE = re.compile(r"^```(?:json|markdown|md)?\s*\n(.*?)\n```$", re.S)
_OPTION_RE = re.compile(r"^\s*([A-Da-d])\s*[\.、．:：)）]\s*(.+?)\s*$")
_KP_SPLIT_RE = re.compile(r"[、,，;；/]+")

_TYPE_ALIASES = {
    "single_choice": "single_choice",
    "choice": "single_choice",
    "选择题": "single_choice",
    "单选题": "single_choice",
    "multiple_choice": "single_choice",
    "多选题": "single_choice",
    "fill_blank": "fill_blank",
    "fill": "fill_blank",
    "blank": "fill_blank",
    "填空题": "fill_blank",
    "calculation": "calculation",
    "proof": "calculation",
    "short_answer": "calculation",
    "解答题": "calculation",
    "计算题": "calculation",
    "证明题": "calculation",
    "判断题": "single_choice",
    "简答题": "calculation",
}
_DIFFICULTY_ALIASES = {
    "easy": "easy",
    "易": "easy",
    "简单": "easy",
    "基础": "easy",
    "medium": "medium",
    "中": "medium",
    "中等": "medium",
    "普通": "medium",
    "hard": "hard",
    "难": "hard",
    "困难": "hard",
}


class _RawKnowledgePoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    parent: str | None = None
    description: str = ""

    @field_validator("name", "description", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return "" if value is None else str(value)


class _RawQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stem: str = ""
    type: str = ""
    options: list[Any] = Field(default_factory=list)
    answer: str = ""
    analysis: str = ""
    difficulty: str = ""
    knowledge_points: list[Any] = Field(default_factory=list)
    page: Any = None
    chunk: Any = None

    @field_validator("stem", "type", "answer", "analysis", "difficulty", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return "" if value is None else str(value)

    @field_validator("options", mode="before")
    @classmethod
    def _options(cls, value: Any) -> list[Any]:
        if isinstance(value, dict):
            return [{"key": key, "text": text} for key, text in value.items()]
        if isinstance(value, str):
            return [value] if value.strip() else []
        return list(value or [])

    @field_validator("knowledge_points", mode="before")
    @classmethod
    def _knowledge_points(cls, value: Any) -> list[Any]:
        if isinstance(value, str):
            return [part for part in _KP_SPLIT_RE.split(value) if part.strip()]
        return list(value or [])


class _RawBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    knowledge_points: list[_RawKnowledgePoint] = Field(
        default_factory=list,
        validation_alias=AliasChoices("knowledge_points", "knowledgePoints", "knowledge"),
    )
    questions: list[_RawQuestion] = Field(
        default_factory=list,
        validation_alias=AliasChoices("questions", "question", "items"),
    )

    @field_validator("knowledge_points", mode="before")
    @classmethod
    def _points(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [{"name": item} if isinstance(item, str) else item for item in value]
        return value


@dataclass(slots=True)
class LlmExtraction:
    knowledge_points: list[tuple[str, str | None, str]] = field(default_factory=list)
    questions: list[ExtractedQuestion] = field(default_factory=list)
    batches: int = 0
    failed_batches: int = 0


def _strip_fence(text: str) -> str:
    text = text.strip()
    match = _FENCE_RE.match(text)
    return match.group(1).strip() if match else text


def _parse_batch(raw: str) -> _RawBatch:
    text = _strip_fence(raw)
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ProviderUnavailable(f"结构化模型返回了非 JSON 内容：{text[:160]}") from None
        try:
            data = json.loads(text[start : end + 1])
        except ValueError as exc:
            raise ProviderUnavailable(f"结构化模型 JSON 解析失败：{text[:160]}") from exc
    if isinstance(data, list):
        data = {"questions": data}
    if not isinstance(data, dict):
        raise ProviderUnavailable("结构化模型返回的 JSON 不是对象")
    return _RawBatch.model_validate(data)


def _render_batch(batch: list[tuple[int, Chunk]]) -> str:
    parts: list[str] = []
    for number, chunk in batch:
        heading = chunk.heading_path or "（无标题）"
        parts.append(
            f"[片段 {number} | 第 {chunk.page_start} 页 | 标题：{heading}]\n{chunk.content}"
        )
    return "\n\n".join(parts)


def _make_batches(
    chunks: list[tuple[int, Chunk]], max_chars: int
) -> list[list[tuple[int, Chunk]]]:
    batches: list[list[tuple[int, Chunk]]] = []
    current: list[tuple[int, Chunk]] = []
    size = 0
    for number, chunk in chunks:
        length = chunk.char_count
        if current and size + length > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append((number, chunk))
        size += length
    if current:
        batches.append(current)
    return batches


def _clean_options(raw: list[Any]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            key = str(item.get("key") or item.get("label") or "").strip()
            text = str(item.get("text") or item.get("content") or "").strip()
        else:
            text = str(item).strip()
            match = _OPTION_RE.match(text)
            key = match.group(1).upper() if match else ""
            if match:
                text = match.group(2).strip()
        if not text:
            continue
        options.append({"key": key.upper() or chr(65 + len(options) % 26), "text": text})
        if len(options) >= 8:
            break
    return options


def _clean_names(raw: list[Any]) -> list[str]:
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            item = item.get("name") or item.get("text") or ""
        text = str(item).strip()
        if not text:
            continue
        for part in _KP_SPLIT_RE.split(text):
            name = part.strip()
            if name and name not in names:
                names.append(name[:80])
    return names


def _qtype(raw: str, options: list[dict[str, str]], stem: str) -> str:
    key = raw.strip().lower()
    if key in _TYPE_ALIASES:
        return _TYPE_ALIASES[key]
    if len(options) >= 2:
        return "single_choice"
    if "____" in stem or "＿＿" in stem:
        return "fill_blank"
    return "calculation"


def _difficulty(raw: str) -> str:
    return _DIFFICULTY_ALIASES.get(raw.strip().lower(), "medium")


def _page(value: Any, fallback: int) -> int:
    try:
        page = int(value)
    except (TypeError, ValueError):
        return fallback
    return page if page > 0 else fallback


def _to_question(
    raw: _RawQuestion, chunks: dict[int, Chunk]
) -> ExtractedQuestion | None:
    stem = raw.stem.strip()
    if len(stem) < 6 or len(stem) > 2000:
        return None
    try:
        chunk_no = int(raw.chunk) if raw.chunk is not None else 0
    except (TypeError, ValueError):
        chunk_no = 0
    chunk = chunks.get(chunk_no)
    page = _page(raw.page, chunk.page_start if chunk else 0)
    options = _clean_options(raw.options)
    return ExtractedQuestion(
        stem=stem,
        answer=raw.answer.strip()[:4000],
        analysis=raw.analysis.strip()[:4000],
        qtype=_qtype(raw.type, options, stem),
        options=options,
        page=page,
        kp_names=_clean_names(raw.knowledge_points),
        difficulty=_difficulty(raw.difficulty),
        origin="llm",
    )


async def _call_batch(
    llm: LlmProvider, batch: list[tuple[int, Chunk]], *, subject: str, stage: str, filename: str
) -> _RawBatch:
    messages = [
        LlmMessage(role="system", content=SYSTEM_PROMPT),
        LlmMessage(
            role="user",
            content=(
                f"学科：{subject}；学段：{stage}；来源文件：{filename}\n\n"
                f"请处理以下片段：\n\n{_render_batch(batch)}"
            ),
        ),
    ]
    raw = await llm.complete(
        messages,
        temperature=settings.llm_default_temperature,
        response_format="json_object",
    )
    try:
        return _parse_batch(raw)
    except (ProviderUnavailable, ValidationError, ValueError) as first:
        log.warning("llm_batch_repair", error=str(first)[:200])
        repaired = await llm.complete(
            [
                *messages,
                LlmMessage(role="assistant", content=raw[:4000]),
                LlmMessage(role="user", content=REPAIR_PROMPT),
            ],
            temperature=0.0,
            response_format="json_object",
        )
        return _parse_batch(repaired)


async def extract_with_llm(
    llm: LlmProvider,
    chunks: list[Chunk],
    *,
    subject: str,
    stage: str,
    filename: str,
) -> LlmExtraction | None:
    """Return LLM-extracted knowledge points/questions, or ``None`` if unused."""
    if not settings.llm_structuring or not chunks:
        return None

    numbered = list(enumerate(chunks, start=1))
    batches = _make_batches(numbered, max(1000, settings.llm_max_chars))
    if not batches:
        return None
    chunk_lookup = dict(numbered)
    semaphore = asyncio.Semaphore(max(1, settings.llm_concurrency))

    async def run(batch: list[tuple[int, Chunk]]) -> _RawBatch | None:
        try:
            async with semaphore:
                return await _call_batch(
                    llm, batch, subject=subject, stage=stage, filename=filename
                )
        except Exception as exc:  # noqa: BLE001 - one batch must not fail the job
            log.warning("llm_batch_failed", error=str(exc)[:300])
            return None

    results = await asyncio.gather(*(run(batch) for batch in batches))
    succeeded = [item for item in results if item is not None]
    failed = len(results) - len(succeeded)
    if not succeeded:
        log.warning("llm_structuring_unavailable", batches=len(batches))
        return None

    points: list[tuple[str, str | None, str]] = []
    seen_points: set[str] = set()
    questions: list[ExtractedQuestion] = []
    seen_questions: set[str] = set()
    for result in succeeded:
        for point in result.knowledge_points:
            name = point.name.strip()[:80]
            key = normalize_kp_name(name)
            if not key or key in seen_points:
                continue
            seen_points.add(key)
            points.append((name, point.parent.strip() if point.parent else None, point.description))
        for raw_question in result.questions:
            question = _to_question(raw_question, chunk_lookup)
            if question is None:
                continue
            key = stem_key(question.stem)
            if key in seen_questions:
                continue
            seen_questions.add(key)
            questions.append(question)

    log.info(
        "llm_structuring_done",
        batches=len(batches),
        failed_batches=failed,
        knowledge_points=len(points),
        questions=len(questions),
    )
    return LlmExtraction(
        knowledge_points=points,
        questions=questions,
        batches=len(batches),
        failed_batches=failed,
    )
