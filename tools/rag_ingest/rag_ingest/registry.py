from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import PROCESSED_DIR, RAW_DIR, REGISTRY_PATH


@dataclass
class Chapter:
    no: int
    title: str
    pdf_page: int
    printed_page: int | None = None


@dataclass
class DocSpec:
    doc_id: str
    subject: str
    stage: str
    title: str
    file: str
    page_offset: int = 0
    doc_type: str = "knowledge_handbook"
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return RAW_DIR / self.file

    @property
    def out_dir(self) -> Path:
        return PROCESSED_DIR / self.doc_id

    def chapter_at(self, pdf_page: int) -> Chapter | None:
        hit = None
        for ch in self.chapters:
            if ch.pdf_page <= pdf_page:
                hit = ch
            else:
                break
        return hit


def _chapter(raw: dict, page_offset: int) -> Chapter:
    if "pdf_page" in raw:
        pdf_page = int(raw["pdf_page"])
        printed = raw.get("printed_page", pdf_page - page_offset)
    else:
        printed = int(raw["printed_page"])
        pdf_page = printed + page_offset
    return Chapter(
        no=int(raw["no"]),
        title=str(raw["title"]).strip(),
        pdf_page=pdf_page,
        printed_page=int(printed),
    )


def load_docs() -> list[DocSpec]:
    if not REGISTRY_PATH.is_file():
        raise FileNotFoundError(f"缺少语料注册表：{REGISTRY_PATH}")
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    docs: list[DocSpec] = []
    for item in data["documents"]:
        offset = int(item.get("page_offset", 0))
        chapters = [_chapter(c, offset) for c in item.get("chapters", [])]
        chapters.sort(key=lambda c: c.pdf_page)
        docs.append(
            DocSpec(
                doc_id=item["doc_id"],
                subject=item["subject"],
                stage=item.get("stage", "high_school"),
                title=item["title"],
                file=item["file"],
                page_offset=offset,
                doc_type=item.get("doc_type", "knowledge_handbook"),
                chapters=chapters,
            )
        )
    return docs


def get_doc(doc_id: str) -> DocSpec:
    for d in load_docs():
        if d.doc_id == doc_id:
            return d
    raise KeyError(f"未知 doc_id：{doc_id}")
