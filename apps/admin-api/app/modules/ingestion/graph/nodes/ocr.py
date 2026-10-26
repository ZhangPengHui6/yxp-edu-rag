"""Node 2: OCR / layout parsing with a one-shot provider fallback."""

from __future__ import annotations

from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.infra.providers import OcrDocument, OcrProvider
from app.modules.ingestion.graph import deps
from app.modules.ingestion.graph.base import IngestionNode, NodeResult
from app.modules.ingestion.graph.state import IngestionState

log = get_logger("ingestion.ocr")


async def parse_with_fallback(
    provider: OcrProvider, *, content: bytes, filename: str
) -> tuple[OcrDocument, str]:
    """Parse with the configured provider, falling back once to the other one."""
    try:
        return await provider.parse(content=content, filename=filename), provider.name
    except ProviderUnavailable as exc:
        fallback = deps.get_ocr_fallback(provider.name)
        if fallback is None:
            raise
        log.warning(
            "ocr_fallback",
            primary=provider.name,
            fallback=fallback.name,
            error=str(exc)[:300],
        )
        return await fallback.parse(content=content, filename=filename), fallback.name


class OcrNode(IngestionNode):
    key = "ocr"
    position = 1
    simulated_detail = "版面与公式识别完成（模拟流水线）"

    async def process(self, state: IngestionState) -> NodeResult:
        provider = state["runtime"].provider
        if provider is None:
            raise ProviderUnavailable("未配置 OCR Provider")
        document, ocr_name = await parse_with_fallback(
            provider,
            content=state["content"],
            filename=state["filename"],
        )
        if not document.markdown.strip():
            raise ProviderUnavailable("OCR 返回内容为空")
        pages = document.page_count or state["runtime"].job.pages
        return NodeResult(
            update={"document": document, "ocr_name": ocr_name, "pages": pages},
            detail=f"{ocr_name} 解析完成：{pages} 页 / {len(document.blocks)} 个版面块",
        )
