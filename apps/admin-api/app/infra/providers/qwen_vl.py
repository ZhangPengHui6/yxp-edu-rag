"""Qwen-VL OCR provider.

Renders a PDF (or accepts a single image) and sends every page to the
OpenAI-compatible Qwen-VL endpoint. Output is page-anchored Markdown, so the
chunker and the LLM structuring step can reuse it unchanged.
"""

from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path

import httpx
import pymupdf
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.infra.providers import OcrBlock, OcrDocument

PAGE_MARK = "<!-- page: {n} -->"
RETRYABLE = {429, 500, 502, 503, 504}

PROMPT = """你是教材数字化助手。把这张知识手册页面完整转写为 Markdown。

要求：
1. 保留原有标题层级，用 # / ## / ### 表示，不要自造标题；
2. 所有数学公式用 LaTeX：行内 $...$，独立公式 $$...$$，化学式同理；
3. 表格转成 Markdown 表格，保留表头；
4. 示意图、函数图像用 ![图:简述](figure) 占位，并写一句图意；
5. 不要输出任何解释、前言、总结或代码块围栏；
6. 只输出页面正文的 Markdown。
"""

_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _render_pages(content: bytes, filename: str) -> list[tuple[int, bytes, str]]:
    suffix = Path(filename).suffix.lower()
    if suffix in _IMAGE_TYPES:
        return [(1, content, _IMAGE_TYPES[suffix])]
    if suffix != ".pdf":
        raise ProviderUnavailable(f"Qwen-VL 不支持的文件类型：{suffix or '(无扩展名)'}")

    try:
        document = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - pymupdf raises its own errors
        raise ProviderUnavailable(f"PDF 打开失败，无法用 Qwen-VL 解析：{exc}") from exc

    pages: list[tuple[int, bytes, str]] = []
    try:
        for page_no in range(1, int(document.page_count) + 1):
            page = document.load_page(page_no - 1)
            pixmap = page.get_pixmap(dpi=settings.ocr_dpi)
            pages.append((page_no, pixmap.tobytes("jpeg", jpg_quality=90), "image/jpeg"))
    finally:
        document.close()
    return pages


def _strip_fence(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:markdown|md)?\s*\n(.*?)\n```$", text, re.S)
    return match.group(1).strip() if match else text


def _message_text(message: dict) -> str:
    """Qwen-VL returns a string, but some gateways return a list of parts."""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") in {None, "text"}
        ]
        return "\n".join(part for part in parts if part).strip()
    return str(content or "").strip()


class QwenVlOcrProvider:
    name = "qwen_vl"

    def __init__(self) -> None:
        self.api_key = settings.dashscope_api_key.strip()
        self.base_url = settings.dashscope_base_url.rstrip("/")
        self.model = settings.qwen_vl_model
        self.concurrency = max(1, settings.qwen_vl_concurrency)
        if not self.api_key:
            raise ProviderUnavailable("DASHSCOPE_API_KEY 未配置，无法调用 Qwen-VL")

    async def parse(self, *, content: bytes, filename: str) -> OcrDocument:
        pages = await run_in_threadpool(_render_pages, content, filename)
        if not pages:
            raise ProviderUnavailable("文件没有可解析的页面")
        semaphore = asyncio.Semaphore(self.concurrency)
        async with httpx.AsyncClient(timeout=180) as client:
            results = await asyncio.gather(
                *(self._one(client, semaphore, data, mime) for _, data, mime in pages)
            )

        lines: list[str] = []
        blocks: list[OcrBlock] = []
        for (page_no, _, _), text in zip(pages, results, strict=True):
            text = _strip_fence(text)
            lines.append(PAGE_MARK.format(n=page_no))
            if text:
                lines.append(text)
                blocks.append(
                    OcrBlock(
                        index=page_no - 1,
                        type="正文",
                        content=text,
                        confidence=1.0,
                        page=page_no,
                    )
                )
        return OcrDocument(
            markdown="\n\n".join(lines) + "\n",
            page_count=len(pages),
            blocks=blocks,
            raw={"provider": "qwen_vl", "model": self.model, "filename": filename},
        )

    async def _one(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        data: bytes,
        mime: str,
    ) -> str:
        data_url = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
        payload = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": settings.qwen_vl_max_tokens,
            "enable_thinking": settings.llm_enable_thinking,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last: Exception | None = None
        for attempt in range(4):
            try:
                async with semaphore:
                    response = await client.post(
                        f"{self.base_url}/chat/completions", headers=headers, json=payload
                    )
                if response.status_code in RETRYABLE:
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                body = response.json()
                return _message_text(body["choices"][0]["message"])
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                last = exc
                if attempt < 3:
                    await asyncio.sleep(min(2**attempt, 8))
        raise ProviderUnavailable(f"Qwen-VL 页面识别失败：{last}")
