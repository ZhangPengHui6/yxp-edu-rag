from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import settings
from .registry import DocSpec

PAGE_RE = re.compile(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
MIN_CHARS = 30


@dataclass
class Block:
    text: str
    page: int
    level: int = 0
    title: str = ""


@dataclass
class Section:
    level: int
    title: str
    page: int
    blocks: list[Block] = field(default_factory=list)


def parse_blocks(md: str) -> list[Block]:
    blocks: list[Block] = []
    page = 0
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        text = "\n".join(buf).strip()
        buf.clear()
        if not text:
            return
        lines = text.splitlines()
        m = HEADING_RE.match(lines[0])
        if m:
            rest = "\n".join(lines[1:]).strip()
            blocks.append(
                Block(text=rest, page=page, level=len(m.group(1)), title=m.group(2).strip())
            )
        else:
            blocks.append(Block(text=text, page=page))

    for line in md.splitlines():
        if not line.strip():
            flush()
            continue
        m = PAGE_RE.match(line)
        if m:
            flush()
            page = int(m.group(1))
            continue
        buf.append(line)
    flush()
    return blocks


def build_sections(blocks: list[Block]) -> list[Section]:
    sections: list[Section] = []
    cur = Section(0, "", blocks[0].page if blocks else 0)
    for b in blocks:
        if b.level:
            if cur.blocks or cur.title:
                sections.append(cur)
            cur = Section(b.level, b.title, b.page)
            if b.text.strip():
                cur.blocks.append(Block(b.text, b.page))
        else:
            cur.blocks.append(b)
    if cur.blocks or cur.title:
        sections.append(cur)
    return sections


def split_section(sec: Section, size: int, overlap: int) -> list[list[Block]]:
    blocks = [b for b in sec.blocks if b.text.strip()]
    if not blocks:
        return []
    groups: list[list[Block]] = []
    cur: list[Block] = []
    cur_len = 0
    for b in blocks:
        length = len(b.text) + 2
        if cur and cur_len + length > size:
            groups.append(cur)
            if len(cur) > 1 and len(cur[-1].text) <= overlap:
                cur = [cur[-1]]
                cur_len = len(cur[0].text) + 2
            else:
                cur = []
                cur_len = 0
        cur.append(b)
        cur_len += length
    if cur:
        groups.append(cur)
    return groups


def build_chunks(doc: DocSpec, md: str) -> list[dict]:
    sections = build_sections(parse_blocks(md))
    size = settings.chunk_size
    overlap = settings.chunk_overlap

    chunks: list[dict] = []
    stack: list[tuple[int, str]] = []

    for sec in sections:
        while stack and stack[-1][0] >= sec.level and sec.level > 0:
            stack.pop()
        if sec.level > 0:
            stack.append((sec.level, sec.title))
        heading_path = " / ".join(t for _, t in stack) or sec.title

        for group in split_section(sec, size, overlap):
            text = "\n\n".join(b.text.strip() for b in group).strip()
            if len(text) < MIN_CHARS:
                continue
            pages = [b.page for b in group if b.page > 0]
            page_start = min(pages) if pages else sec.page
            page_end = max(pages) if pages else page_start
            chapter = doc.chapter_at(page_start)
            idx = len(chunks)
            chunks.append(
                {
                    "chunk_id": f"{doc.doc_id}:{idx:04d}",
                    "doc_id": doc.doc_id,
                    "doc_title": doc.title,
                    "doc_type": doc.doc_type,
                    "subject": doc.subject,
                    "stage": doc.stage,
                    "chapter_no": chapter.no if chapter else None,
                    "chapter": chapter.title if chapter else None,
                    "section": sec.title or None,
                    "heading_path": heading_path,
                    "page_start": page_start,
                    "page_end": page_end,
                    "printed_page_start": page_start - doc.page_offset,
                    "source_file": doc.file,
                    "char_count": len(text),
                    "text": text,
                    "embed_text": f"{heading_path}\n{text}",
                }
            )
    return chunks


def write_chunks(doc: DocSpec, chunks: list[dict]) -> Path:
    path = doc.out_dir / "chunks.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return path
