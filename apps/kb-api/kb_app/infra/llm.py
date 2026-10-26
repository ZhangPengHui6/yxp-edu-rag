"""LLM generation and reranking, reusing the admin provider adapters."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.infra.providers import LlmMessage, get_llm_provider, get_rerank_provider

from kb_app.config import get_kb_settings


async def generate(
    query: str,
    *,
    context: str,
    history: list[dict[str, str]],
    system_prompt: str,
) -> tuple[str, str]:
    provider = get_llm_provider()
    if provider.name == "unconfigured":
        raise ProviderUnavailable("DASHSCOPE_API_KEY 未配置，无法生成回答")

    messages = [LlmMessage(role="system", content=system_prompt)]
    for item in history:
        role = item.get("role", "user")
        content = (item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append(LlmMessage(role=role, content=content))
    messages.append(
        LlmMessage(
            role="user",
            content=_user_prompt(query=query, context=context),
        )
    )
    answer = await provider.complete(
        messages,
        temperature=0.2,
        max_tokens=get_kb_settings().kb_answer_max_tokens,
    )
    model = getattr(provider, "model", provider.name)
    return answer, str(model)


async def rerank(
    query: str, documents: list[str], *, top_n: int
) -> tuple[list[tuple[int, float]], str]:
    provider = get_rerank_provider()
    if provider.name == "unconfigured":
        return [], provider.name
    return await provider.rerank_with_scores(query, documents, top_n=top_n), provider.name


async def stream_answer(
    query: str,
    *,
    context: str,
    history: list[dict[str, str]],
    system_prompt: str,
) -> AsyncIterator[str]:
    """Yield answer deltas from the OpenAI-compatible chat endpoint."""
    if not settings.dashscope_api_key.strip():
        raise ProviderUnavailable("DASHSCOPE_API_KEY 未配置，无法生成回答")

    messages = [{"role": "system", "content": system_prompt}]
    for item in history:
        role = item.get("role", "user")
        content = (item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": _user_prompt(query=query, context=context)})

    payload: dict[str, Any] = {
        "model": settings.llm_default_model,
        "temperature": 0.2,
        "max_tokens": get_kb_settings().kb_answer_max_tokens,
        "enable_thinking": settings.llm_enable_thinking,
        "stream": True,
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key.strip()}",
        "Content-Type": "application/json",
    }
    url = f"{settings.dashscope_base_url.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "ignore")
                raise ProviderUnavailable(
                    f"模型流式请求失败（HTTP {response.status_code}）：{body[:300]}"
                )
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta", {}).get("content")
                except (ValueError, KeyError, IndexError, TypeError):
                    continue
                if delta:
                    yield str(delta)


def _user_prompt(*, query: str, context: str) -> str:
    return (
        "【教材与题目资料】\n"
        f"{context}\n\n"
        "【学生问题】\n"
        f"{query}\n\n"
        "请严格依据上面的资料回答；引用时在句末标注对应编号，例如 [1]。"
        "如果资料不足以回答，请明确说明缺少哪部分内容。"
    )
