from __future__ import annotations

import asyncio
import base64
import json
import os
import re

import httpx
import pymupdf

from ..config import CACHE_DIR, settings
from ..pdf_render import load_pages, render_doc
from ..registry import DocSpec
from .base import OcrError, OcrResult

PROMPT = """你是教材数字化助手。把这张知识手册页面完整转写为 Markdown。

要求：
1. 保留原有标题层级，用 # / ## / ### 表示，不要自造标题；
2. 所有数学公式用 LaTeX：行内 $...$，独立公式 $$...$$，化学式同理；
3. 表格转成 Markdown 表格，保留表头；
4. 示意图、函数图像用 ![图:简述](figure) 占位，并写一句图意；
5. 不要输出任何解释、前言、总结或代码块围栏；
6. 只输出页面正文的 Markdown。
"""

PAGE_MARK = "<!-- page: {n} -->"


class QwenVlProvider:
    """逐页调用 DashScope OpenAI 兼容接口的 Qwen2.5-VL。"""

    name = "qwen_vl"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = (api_key or settings.dashscope_api_key).strip()
        self.model = model or settings.qwen_vl_model
        self.base_url = settings.dashscope_base_url
        self.concurrency = int(os.getenv("OCR_CONCURRENCY", "4"))
        if not self.api_key:
            raise OcrError("缺少 DASHSCOPE_API_KEY：请在项目根目录 .env 中配置")

    def run(self, doc: DocSpec, force: bool = False) -> OcrResult:
        out_dir = doc.out_dir / "ocr"
        out_dir.mkdir(parents=True, exist_ok=True)
        book_md = out_dir / "book.md"
        if book_md.is_file() and not force:
            return OcrResult(self.name, book_md, None, None)

        pages = load_pages(doc) or render_doc(doc)
        targets = [p for p in pages if not p.is_blank]
        img_dir = CACHE_DIR / doc.doc_id / "ocr_jpg"
        img_dir.mkdir(parents=True, exist_ok=True)
        jpgs = _render_jpgs(doc, [p.page_no for p in targets], img_dir)

        results = asyncio.run(self._ocr_all(jpgs))

        page_dir = out_dir / "pages"
        page_dir.mkdir(parents=True, exist_ok=True)
        parts: list[str] = []
        for page_no in sorted(results):
            text = results[page_no].strip()
            (page_dir / f"p{page_no:03d}.md").write_text(text + "\n", encoding="utf-8")
            parts.append(PAGE_MARK.format(n=page_no))
            parts.append(text)
        book_md.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        return OcrResult(self.name, book_md, None, img_dir)

    async def _ocr_all(self, jpgs: dict[int, str]) -> dict[int, str]:
        sem = asyncio.Semaphore(self.concurrency)
        async with httpx.AsyncClient(timeout=180) as client:
            tasks = {no: self._one(client, sem, path) for no, path in jpgs.items()}
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        out: dict[int, str] = {}
        for page_no, res in zip(tasks.keys(), results):
            if isinstance(res, Exception):
                raise OcrError(f"第 {page_no} 页 OCR 失败：{res}") from res
            out[page_no] = res
        return out

    async def _one(self, client: httpx.AsyncClient, sem: asyncio.Semaphore, path: str) -> str:
        data_url = "data:image/jpeg;base64," + base64.b64encode(open(path, "rb").read()).decode()
        payload = {
            "model": self.model,
            "temperature": 0.0,
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
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last: Exception | None = None
        for attempt in range(4):
            try:
                async with sem:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions", headers=headers, json=payload
                    )
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                resp.raise_for_status()
                body = resp.json()
                return _strip_fence(body["choices"][0]["message"]["content"])
            except Exception as exc:  # noqa: BLE001 - 重试后统一抛出
                last = exc
                await asyncio.sleep(2**attempt)
        raise OcrError(str(last))


def _strip_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```$", text, re.S)
    return m.group(1).strip() if m else text


def _render_jpgs(doc: DocSpec, page_numbers: list[int], out_dir) -> dict[int, str]:
    paths: dict[int, str] = {}
    with pymupdf.open(doc.path) as pdf:
        for no in page_numbers:
            path = out_dir / f"p{no:03d}.jpg"
            if not path.exists():
                pdf[no - 1].get_pixmap(dpi=int(os.getenv("OCR_DPI", "200"))).save(
                    path, jpg_quality=90
                )
            paths[no] = str(path)
    return paths
