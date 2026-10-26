from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..registry import DocSpec


class OcrError(RuntimeError):
    pass


@dataclass
class OcrResult:
    provider: str
    book_md: Path
    content_list: Path | None = None
    assets_dir: Path | None = None


class OcrProvider(Protocol):
    name: str

    def run(self, doc: DocSpec, force: bool = False) -> OcrResult: ...


def get_provider(name: str) -> OcrProvider:
    name = name.strip().lower()
    if name == "mineru":
        from .mineru import MineruProvider

        return MineruProvider()
    if name in {"qwen_vl", "qwen", "dashscope"}:
        from .qwen_vl import QwenVlProvider

        return QwenVlProvider()
    raise OcrError(f"未知 OCR provider：{name}（可选 mineru / qwen_vl）")
