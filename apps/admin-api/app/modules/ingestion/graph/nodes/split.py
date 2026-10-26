"""Node 3: split page-anchored Markdown into retrieval chunks."""

from __future__ import annotations

from app.core.errors import ProviderUnavailable
from app.modules.ingestion.chunker import build_chunks
from app.modules.ingestion.graph.base import IngestionNode, NodeResult
from app.modules.ingestion.graph.state import IngestionState


class SplitNode(IngestionNode):
    key = "split"
    position = 2
    simulated_detail = "题目切分完成（模拟流水线）"

    async def process(self, state: IngestionState) -> NodeResult:
        chunks = build_chunks(state["document"].markdown)
        if not chunks:
            raise ProviderUnavailable("解析结果中未能切分出有效片段")
        average = round(sum(chunk.char_count for chunk in chunks) / len(chunks))
        return NodeResult(
            update={"chunks": chunks, "average_chunk_chars": average},
            detail=f"切分得到 {len(chunks)} 个片段，平均 {average} 字",
        )
