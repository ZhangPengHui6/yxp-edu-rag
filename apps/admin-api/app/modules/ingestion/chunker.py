"""Turn OCR Markdown into retrieval chunks.

The Markdown produced by the OCR provider carries ``<!-- page: N -->`` anchors
and ``#`` headings. Chunks keep whole paragraphs/tables/formulas together and
inherit the heading path plus page range so the admin UI can trace evidence back
to the PDF.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.core.config import settings

PAGE_RE = re.compile(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
SENTENCE_RE = re.compile(r"(?<=[。！？；])")


@dataclass(slots=True)
class Block:
    text: str
    page: int
    level: int = 0
    title: str = ""
    kind: str = "text"


@dataclass(slots=True)
class Section:
    level: int
    title: str
    page: int
    headings: list[str] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)


@dataclass(slots=True)
class Chunk:
    content: str
    heading_path: str
    section_title: str
    level: int
    content_type: str
    page_start: int
    page_end: int
    char_count: int
    token_count: int
    hash: str


def parse_blocks(markdown: str) -> list[Block]:
    blocks: list[Block] = []
    page = 0
    buf: list[str] = []
    in_formula = False

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        text = "\n".join(buf).strip()
        buf = []
        if not text:
            return
        lines = text.splitlines()
        match = HEADING_RE.match(lines[0])
        if match:
            rest = "\n".join(lines[1:]).strip()
            blocks.append(
                Block(
                    text=rest,
                    page=page,
                    level=len(match.group(1)),
                    title=match.group(2).strip(),
                    kind="heading",
                )
            )
        else:
            blocks.append(Block(text=text, page=page, kind=_kind_of(text)))

    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            if in_formula:
                buf.append(line)
                continue
            flush()
            continue
        page_match = PAGE_RE.match(line)
        if page_match and not in_formula:
            flush()
            page = int(page_match.group(1))
            continue
        if stripped.startswith("$$"):
            buf.append(line)
            if stripped.count("$$") % 2 == 1:
                in_formula = not in_formula
                if not in_formula:
                    flush()
            continue
        if in_formula:
            buf.append(line)
            continue
        buf.append(line)
    flush()
    return blocks


def _kind_of(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and all(line.lstrip().startswith("|") for line in lines):
        return "table"
    if text.lstrip().startswith("$$"):
        return "formula"
    return "text"


def build_sections(blocks: list[Block]) -> list[Section]:
    sections: list[Section] = []
    headings: list[tuple[int, str]] = []
    current = Section(level=0, title="", page=blocks[0].page if blocks else 0)

    for block in blocks:
        if block.level:
            if current.blocks or current.title:
                sections.append(current)
            while headings and headings[-1][0] >= block.level:
                headings.pop()
            headings.append((block.level, block.title))
            current = Section(
                level=block.level,
                title=block.title,
                page=block.page,
                headings=[title for _, title in headings],
            )
            if block.text.strip():
                current.blocks.append(Block(block.text, block.page, kind=block.kind))
            continue
        current.blocks.append(block)

    if current.blocks or current.title:
        sections.append(current)
    return sections


def _split_long(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    sentences = [part for part in SENTENCE_RE.split(text) if part.strip()]
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > limit:
            pieces.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        pieces.append(current)
    return pieces or [text]


def chunk_section(section: Section, *, limit: int, overlap: int) -> list[Chunk]:
    heading_path = " / ".join(section.headings) if section.headings else section.title
    section_title = section.title or heading_path
    chunks: list[Chunk] = []
    buffer: list[Block] = []
    size = 0

    def emit() -> None:
        nonlocal buffer, size
        if not buffer:
            return
        content = "\n\n".join(block.text for block in buffer).strip()
        if content:
            chunks.append(
                _make_chunk(
                    content,
                    heading_path=heading_path,
                    section_title=section_title,
                    level=section.level,
                    page_start=min(block.page for block in buffer),
                    page_end=max(block.page for block in buffer),
                    kind=_dominant_kind(buffer),
                )
            )
        if overlap > 0 and len(buffer) > 1:
            tail = buffer[-1]
            if len(tail.text) <= overlap:
                buffer = [tail]
                size = len(tail.text)
                return
        buffer = []
        size = 0

    for block in section.blocks:
        parts = _split_long(block.text, limit)
        for part in parts:
            piece = Block(part, block.page, kind=block.kind)
            if buffer and size + len(part) > limit:
                emit()
            buffer.append(piece)
            size += len(part)
    emit()
    return chunks


def _dominant_kind(blocks: list[Block]) -> str:
    kinds = [block.kind for block in blocks if block.kind]
    if kinds.count("table") * 2 >= len(kinds):
        return "table"
    if kinds.count("formula") * 2 >= len(kinds):
        return "formula"
    return "text"


def _make_chunk(
    content: str,
    *,
    heading_path: str,
    section_title: str,
    level: int,
    page_start: int,
    page_end: int,
    kind: str,
) -> Chunk:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return Chunk(
        content=content,
        heading_path=heading_path,
        section_title=section_title,
        level=level,
        content_type=kind,
        page_start=page_start,
        page_end=page_end,
        char_count=len(content),
        token_count=max(1, len(content) // 2),
        hash=digest,
    )


def build_chunks(markdown: str) -> list[Chunk]:
    limit = max(200, settings.chunk_size)
    overlap = max(0, min(settings.chunk_overlap, limit // 2))
    chunks: list[Chunk] = []
    for section in build_sections(parse_blocks(markdown)):
        chunks.extend(chunk_section(section, limit=limit, overlap=overlap))
    return [chunk for chunk in chunks if chunk.char_count >= 30]
