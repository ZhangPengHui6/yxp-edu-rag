"""Node 1: read the uploaded object from object storage."""

from __future__ import annotations

from app.core.errors import ProviderUnavailable
from app.modules.ingestion.graph import deps
from app.modules.ingestion.graph.base import IngestionNode, NodeResult
from app.modules.ingestion.graph.state import IngestionState


class UploadNode(IngestionNode):
    key = "upload"
    position = 0
    simulated_detail = "文件已接收，等待解析"

    async def process(self, state: IngestionState) -> NodeResult:
        job = state["runtime"].job
        if not job.source_uri:
            raise FileNotFoundError("任务没有关联源文件 objectKey")

        content = await deps.get_storage().get_bytes(job.source_uri)
        if not content:
            raise ProviderUnavailable("源文件为空，无法解析")

        size_mb = len(content) / 1024 / 1024
        return NodeResult(
            update={"content": content, "filename": job.source_name},
            detail=f"已读取源文件（{size_mb:.1f} MB）",
        )
