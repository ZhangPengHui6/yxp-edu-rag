"""Provider-level tests for Qwen chat / rerank / Qwen-VL (no network)."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.infra.providers import LlmMessage, OcrDocument, qwen, qwen_vl
from app.infra.providers.qwen import (
    QwenLlmProvider,
    QwenLlmRerankProvider,
    _score_list,
)
from app.infra.providers.qwen_vl import QwenVlOcrProvider
from app.modules.ingestion.graph import deps
from app.modules.ingestion.graph.nodes.ocr import parse_with_fallback


def test_score_list_accepts_fenced_json_and_plain_list() -> None:
    assert _score_list('```json\n{"scores": [0.1, 0.4]}\n```', 2) == [0.1, 0.4]
    assert _score_list("[0.9, 0.2]", 2) == [0.9, 0.2]
    with pytest.raises(ProviderUnavailable):
        _score_list('{"scores": [0.1]}', 2)


async def test_chat_provider_sends_json_mode_and_thinking_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    captured: dict[str, Any] = {}

    async def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int,
        attempts: int = 4,
    ) -> dict[str, Any]:
        captured.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    monkeypatch.setattr(qwen, "_post_json", fake_post)
    provider = QwenLlmProvider()
    content = await provider.complete(
        [LlmMessage(role="user", content="输出 JSON")],
        temperature=0.0,
        response_format="json_object",
    )

    assert content == '{"ok": true}'
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["enable_thinking"] is False
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["model"] == settings.llm_default_model


class _StubLlm:
    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.1,
        response_format: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        return '{"scores": [0.2, 0.9, 0.5]}'


async def test_llm_reranker_sorts_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    provider = QwenLlmRerankProvider()
    provider.llm = _StubLlm()  # type: ignore[assignment]

    result = await provider.rerank_with_scores("题目", ["甲", "乙", "丙"], top_n=2)
    assert result == [(1, 0.9), (2, 0.5)]


class _FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "# 标题\n第一页正文"}}]}


class _FakeAsyncClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> _FakeResponse:
        self.payloads.append(json)
        return _FakeResponse()


async def test_qwen_vl_marks_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(
        qwen_vl,
        "_render_pages",
        lambda content, filename: [(1, b"a", "image/png"), (2, b"b", "image/png")],
    )
    monkeypatch.setattr(qwen_vl.httpx, "AsyncClient", _FakeAsyncClient)

    document = await QwenVlOcrProvider().parse(content=b"fake", filename="book.pdf")

    assert document.page_count == 2
    assert "<!-- page: 1 -->" in document.markdown
    assert "<!-- page: 2 -->" in document.markdown
    assert [block.page for block in document.blocks] == [1, 2]
    assert document.blocks[0].content.startswith("# 标题")


class _FailingProvider:
    name = "mineru"

    async def parse(self, *, content: bytes, filename: str) -> OcrDocument:
        raise ProviderUnavailable("mineru down")


class _OkProvider:
    name = "qwen_vl"

    async def parse(self, *, content: bytes, filename: str) -> OcrDocument:
        return OcrDocument(markdown="<!-- page: 1 -->\n正文", page_count=1, blocks=[])


async def test_ocr_falls_back_to_qwen_vl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_ocr_fallback", lambda name: _OkProvider())
    document, name = await parse_with_fallback(
        _FailingProvider(), content=b"fake", filename="book.pdf"
    )
    assert name == "qwen_vl"
    assert document.page_count == 1
