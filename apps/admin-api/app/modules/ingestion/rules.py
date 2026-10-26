"""Ingestion step definitions and status mapping (数据对象 ingestion_job_step)."""

from __future__ import annotations

STEP_DEFS: list[dict[str, str]] = [
    {"key": "upload", "label": "文件上传"},
    {"key": "ocr", "label": "版面与 OCR 解析"},
    {"key": "split", "label": "题目切分"},
    {"key": "struct", "label": "结构化与公式"},
    {"key": "quality", "label": "质量检查"},
]

STEP_KEYS = [item["key"] for item in STEP_DEFS]
STEP_LABELS = {item["key"]: item["label"] for item in STEP_DEFS}

VALID_SOURCE_TYPES = {"PDF", "DOCX", "IMAGE", "ZIP"}
VALID_STAGES = {"primary", "middle", "high"}

INTERNAL_TO_UI = {
    "pending": "pending",
    "running": "running",
    "succeeded": "done",
    "failed": "failed",
    "canceled": "pending",
}


def ui_status(status: str) -> str:
    return INTERNAL_TO_UI.get(status, "pending")


def progress_at(index: int) -> int:
    """Progress after the step at ``index`` completes."""
    return min(100, round((index + 1) / len(STEP_DEFS) * 100))
