"""Qwen / DashScope providers (OpenAI-compatible chat + text rerank).

Both providers read their credentials from the admin API settings, which are
populated from ``.env`` / the process environment. Network errors are retried a
few times and then surfaced as ``ProviderUnavailable`` so the ingestion job can
degrade gracefully.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.infra.providers import LlmMessage

RETRYABLE = {429, 500, 502, 503, 504}
RERANK_PROMPT = """你是教育题库的检索排序器。给定一个题目（query）和若干候选材料，
逐条评估每条材料与题目的相关程度，分数 0~1（1 表示直接给出了该题的答案或解题依据）。

只输出 JSON 对象，不要解释：
{"scores": [0.93, 0.12, ...]}

scores 的长度必须等于候选材料数量，顺序与材料编号一致。"""


def _strip_fence(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json|markdown|md)?\s*\n(.*?)\n```$", text, re.S)
    return match.group(1).strip() if match else text


async def _post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
    attempts: int = 4,
) -> dict[str, Any]:
    last: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(attempts):
            try:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code in RETRYABLE:
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise ProviderUnavailable("模型服务返回了非 JSON 对象")
                return body
            except httpx.HTTPError as exc:
                last = exc
            except ValueError as exc:  # invalid JSON
                last = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(min(2**attempt, 8))
    raise ProviderUnavailable(f"模型服务请求失败：{last}")


class QwenLlmProvider:
    """OpenAI-compatible chat completions (``qwen-flash`` by default)."""

    name = "qwen"

    def __init__(self, model: str | None = None) -> None:
        self.api_key = settings.dashscope_api_key.strip()
        self.base_url = settings.dashscope_base_url.rstrip("/")
        self.model = model or settings.llm_default_model
        if not self.api_key:
            raise ProviderUnavailable("DASHSCOPE_API_KEY 未配置")

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.1,
        response_format: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "enable_thinking": settings.llm_enable_thinking,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
        }
        if response_format:
            payload["response_format"] = {"type": response_format}
        body = await _post_json(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout=settings.llm_timeout_seconds,
        )
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailable(f"LLM 响应格式异常：{body}") from exc
        return str(content).strip()


def _score_list(raw: str, count: int) -> list[float]:
    """Parse ``{"scores": [...]}`` (or a plain list) from a chat response."""
    text = _strip_fence(raw)
    data: Any
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ProviderUnavailable(f"重排模型返回了非 JSON 内容：{text[:200]}") from None
        try:
            data = json.loads(text[start : end + 1])
        except ValueError as exc:
            raise ProviderUnavailable(f"重排模型 JSON 解析失败：{text[:200]}") from exc

    items = data.get("scores") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ProviderUnavailable("重排模型输出缺少 scores 数组")
    scores: list[float] = []
    for item in items:
        if isinstance(item, dict):
            item = item.get("score", item.get("relevance_score"))
        try:
            scores.append(min(1.0, max(0.0, float(item))))
        except (TypeError, ValueError):
            scores.append(0.0)
    if len(scores) != count:
        raise ProviderUnavailable(f"重排模型返回了 {len(scores)} 个分数，期望 {count} 个")
    return scores


class QwenLlmRerankProvider:
    """Chat-model reranker for gateways without a dedicated rerank endpoint.

    Qiniu's OpenAI-compatible endpoint has no text-rerank service, so the
    evidence model (``qwen-flash`` by default) scores candidate passages and the
    ingestion pipeline turns those scores into ``evidence_score`` /
    ``evidence_chunk_ids``.
    """

    name = "qwen-llm-rerank"

    def __init__(self) -> None:
        self.llm = QwenLlmProvider()

    async def rerank_with_scores(
        self, query: str, documents: list[str], *, top_n: int = 5
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        numbered = "\n\n".join(f"[{index}] {text}" for index, text in enumerate(documents))
        raw = await self.llm.complete(
            [
                LlmMessage(role="system", content=RERANK_PROMPT),
                LlmMessage(role="user", content=f"题目：{query}\n\n候选材料：\n{numbered}"),
            ],
            temperature=0.0,
            response_format="json_object",
            max_tokens=1024,
        )
        scores = _score_list(raw, len(documents))
        scored = sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)
        return scored[:top_n]

    async def rerank(self, query: str, documents: list[str], *, top_n: int = 5) -> list[int]:
        return [index for index, _ in await self.rerank_with_scores(query, documents, top_n=top_n)]


class QwenRerankProvider:
    """DashScope text-rerank service (``qwen3-rerank`` by default)."""

    name = "qwen-rerank"

    def __init__(self) -> None:
        self.api_key = settings.rerank_api_key.strip()
        self.base_url = settings.rerank_base_url.rstrip("/")
        self.model = settings.rerank_model
        if not self.api_key or not self.base_url:
            raise ProviderUnavailable("RERANK_API_KEY / RERANK_BASE_URL 未配置")

    async def rerank_with_scores(
        self, query: str, documents: list[str], *, top_n: int = 5
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        payload = {
            "model": self.model,
            "input": {"query": query, "documents": documents},
            "parameters": {"top_n": min(top_n, len(documents)), "return_documents": False},
        }
        url = f"{self.base_url}/services/rerank/text-rerank/text-rerank"
        if self.base_url.endswith("/services/rerank/text-rerank/text-rerank"):
            url = self.base_url
        body = await _post_json(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout=settings.rerank_timeout_seconds,
        )
        results = (body.get("output") or {}).get("results") or []
        scored: list[tuple[int, float]] = []
        for item in results:
            try:
                scored.append((int(item["index"]), float(item["relevance_score"])))
            except (KeyError, TypeError, ValueError):
                continue
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_n]

    async def rerank(self, query: str, documents: list[str], *, top_n: int = 5) -> list[int]:
        return [index for index, _ in await self.rerank_with_scores(query, documents, top_n=top_n)]
