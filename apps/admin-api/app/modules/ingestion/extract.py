"""Derive knowledge points and questions from OCR Markdown.

This is a deterministic first pass: headings become the knowledge-point tree and
blocks introduced by 例/例题/变式/练习 become draft questions. The review flow
then decides whether those drafts are good enough to publish.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.modules.ingestion.chunker import Chunk

PAGE_RE = re.compile(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
QUESTION_START = re.compile(
    r"^\s*(?:【\s*(?:例|例题|变式|练习)\s*\d*\s*】"
    r"|(?:例|例题|变式|练习)\s*(?:\d+\s*[\.、:：)）]?|[\.、:：)）]))\s*"
)
OPTION_RE = re.compile(r"^\s*([A-Da-d])\s*[\.、．:：]\s*(.+?)\s*$")
ANSWER_RE = re.compile(r"^\s*(?:答案|解析|解|分析)\s*[:：]\s*(.*)$")
SKIP_TITLES = {"目录", "前言", "序言", "附录", "后记"}
STAGE_PREFIX = {"primary": "小学", "middle": "初中", "high": "高中"}


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:20]}"


@dataclass(slots=True)
class ExtractedQuestion:
    stem: str
    answer: str
    analysis: str
    qtype: str
    options: list[dict[str, str]]
    page: int
    kp_names: list[str] = field(default_factory=list)
    difficulty: str = "medium"
    origin: str = "rule"  # rule | llm


def collect_knowledge_points(
    chunks: list[Chunk],
    *,
    tenant_id: str,
    subject: str,
    stage: str,
    book_title: str = "",
    existing_ids: dict[tuple[str | None, str], str] | None = None,
) -> list[dict[str, Any]]:
    """Build a heading tree under the subject root and merge repeated paths.

    ``existing_ids`` maps ``(parent_id, name)`` to an already stored node so a
    new book reuses the shared subject tree instead of duplicating names.
    """
    known = existing_ids or {}
    root_name = f"{STAGE_PREFIX.get(stage, '')}{subject}".strip() or "未分类"
    root_id = known.get((None, root_name)) or _stable_id("kp_", tenant_id, "", root_name)
    nodes: dict[str, dict[str, Any]] = {
        root_id: {
            "_id": root_id,
            "tenant_id": tenant_id,
            "subject": subject,
            "stage": stage,
            "name": root_name,
            "level": 0,
            "parent_id": None,
            "order": 0,
            "code": None,
            "description": "",
            "path": root_name,
            "coverage": 0,
            "status": "draft",
            "pitfalls": [],
            "paragraph_count": 0,
            "question_count": 0,
        }
    }
    child_count: dict[str | None, int] = {}

    for chunk in chunks:
        path = heading_names(chunk.heading_path, root_name=root_name, book_title=book_title)
        parent: str | None = root_id
        for depth, name in enumerate(path, start=1):
            if name in SKIP_TITLES or len(name) > 80:
                continue
            node_id = known.get((parent, name)) or _stable_id("kp_", tenant_id, parent or "", name)
            if node_id not in nodes:
                order = child_count.get(parent, 0)
                child_count[parent] = order + 1
                nodes[node_id] = {
                    "_id": node_id,
                    "tenant_id": tenant_id,
                    "subject": subject,
                    "stage": stage,
                    "name": name,
                    "level": depth,
                    "parent_id": parent,
                    "order": order,
                    "code": None,
                    "description": "",
                    "path": " / ".join([root_name, *path[:depth]]),
                    "coverage": 0,
                    "status": "draft",
                    "pitfalls": [],
                    "paragraph_count": 0,
                    "question_count": 0,
                }
            nodes[node_id]["paragraph_count"] += 1
            parent = node_id
        if not path:
            nodes[root_id]["paragraph_count"] += 1

    return list(nodes.values())


def heading_names(heading_path: str, *, root_name: str, book_title: str = "") -> list[str]:
    names = [name.strip() for name in heading_path.split(" / ") if name.strip()]
    if names and book_title and names[0] == book_title:
        names = names[1:]
    if names and names[0] == root_name:
        names = names[1:]
    return names


_PUNCT_RE = re.compile(r"[\s　·・、，,。.：:；;！!？?（）()【】\[\]「」『』<>《》\"'“”‘’_\-—]+")


def normalize_kp_name(name: str) -> str:
    """Case/punctuation-insensitive key used to match LLM names to the tree."""
    return _PUNCT_RE.sub("", name).lower()


def stem_key(stem: str) -> str:
    """Short normalised key used to dedupe rule- and LLM-extracted questions."""
    return re.sub(r"\s+", "", stem).lower()[:80]


def find_kp_id(key: str, by_name: dict[str, str]) -> str | None:
    """Exact match first, then section-number-insensitive suffix match.

    LLM output often drops the numbering ("函数的性质" vs "1.1 函数的性质"), so a
    suffix match is accepted when both names are long enough to be specific.
    """
    node_id = by_name.get(key)
    if node_id is None and len(key) >= 4:
        node_id = next(
            (
                value
                for candidate, value in by_name.items()
                if len(candidate) >= 4
                and (candidate.endswith(key) or key.endswith(candidate))
            ),
            None,
        )
    return node_id


def merge_extracted_questions(
    primary: list[ExtractedQuestion], fallback: list[ExtractedQuestion]
) -> list[ExtractedQuestion]:
    """Keep ``primary`` and append fallback questions whose stem is not present."""
    seen = {stem_key(question.stem) for question in primary}
    merged = list(primary)
    for question in fallback:
        key = stem_key(question.stem)
        if key and key not in seen:
            seen.add(key)
            merged.append(question)
    return merged


def apply_llm_knowledge_points(
    nodes: list[dict[str, Any]],
    hints: Sequence[tuple[str, str | None, str]],
    *,
    tenant_id: str,
    subject: str,
    stage: str,
    root_name: str,
) -> tuple[list[dict[str, Any]], int]:
    """Attach LLM knowledge points to the heading tree.

    ``hints`` holds ``(name, parent_name, description)``. Existing names (heading
    or previously created) are reused and only get a description; new names are
    inserted under their suggested parent, else under the subject root.
    Returns the nodes and the number of newly created ones.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for node in nodes:
        by_name.setdefault(normalize_kp_name(node["name"]), node)
    root = next((node for node in nodes if node["level"] == 0), None)
    if root is None:
        return nodes, 0

    orders: dict[str | None, int] = {}
    for node in nodes:
        parent_id = node.get("parent_id")
        orders[parent_id] = max(orders.get(parent_id, 0), int(node.get("order", 0)) + 1)

    added = 0
    for raw_name, parent_name, description in hints:
        name = (raw_name or "").strip()[:80]
        if not name or name in SKIP_TITLES:
            continue
        key = normalize_kp_name(name)
        if not key:
            continue
        existing = by_name.get(key)
        if existing is None and len(key) >= 4:
            existing = next(
                (
                    node
                    for candidate, node in by_name.items()
                    if len(candidate) >= 4
                    and (candidate.endswith(key) or key.endswith(candidate))
                ),
                None,
            )
        if existing is not None:
            if description and not existing.get("description"):
                existing["description"] = description.strip()[:500]
            continue

        parent = root
        if parent_name:
            parent = by_name.get(normalize_kp_name(parent_name), root)
        parent_id = parent["_id"]
        order = orders.get(parent_id, 0)
        orders[parent_id] = order + 1
        node = {
            "_id": _stable_id("kp_", tenant_id, parent_id, name),
            "tenant_id": tenant_id,
            "subject": subject,
            "stage": stage,
            "name": name,
            "level": int(parent.get("level", 0)) + 1,
            "parent_id": parent_id,
            "order": order,
            "code": None,
            "description": description.strip()[:500],
            "path": " / ".join([str(parent.get("path", root_name)), name]),
            "coverage": 0,
            "status": "draft",
            "pitfalls": [],
            "paragraph_count": 0,
            "question_count": 0,
        }
        nodes.append(node)
        by_name[key] = node
        added += 1
    return nodes, added


def extract_questions(markdown: str, *, subject: str, filename: str) -> list[ExtractedQuestion]:
    questions: list[ExtractedQuestion] = []
    page = 0
    headings: list[str] = []
    current: list[str] | None = None
    current_page = 0
    current_kp: list[str] = []

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        text = "\n".join(current).strip()
        current = None
        if not text:
            return
        question = _parse_question(
            text,
            page=current_page,
            kp_names=list(current_kp),
            subject=subject,
            filename=filename,
        )
        if question is not None:
            questions.append(question)

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        page_match = PAGE_RE.match(line)
        if page_match:
            page = int(page_match.group(1))
            continue
        heading = HEADING_RE.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            headings = headings[: level - 1] + [heading.group(2).strip()]
            continue
        if QUESTION_START.match(line):
            flush()
            current = [QUESTION_START.sub("", line).strip()]
            current_page = page
            current_kp = headings[-1:] if headings else []
            continue
        if current is not None:
            current.append(line)
    flush()
    return questions


def _parse_question(
    block: str,
    *,
    page: int,
    kp_names: list[str],
    subject: str,
    filename: str,
) -> ExtractedQuestion | None:
    lines = [line.strip() for line in block.splitlines()]
    stem_lines: list[str] = []
    answer_lines: list[str] = []
    analysis_lines: list[str] = []
    options: list[dict[str, str]] = []
    bucket = stem_lines
    for line in lines:
        if not line:
            continue
        answer_match = ANSWER_RE.match(line)
        if answer_match:
            label = line.split(":", 1)[0].split("：", 1)[0]
            bucket = analysis_lines if label in {"解析", "分析"} else answer_lines
            rest = answer_match.group(1).strip()
            if rest:
                bucket.append(rest)
            continue
        option_match = OPTION_RE.match(line)
        if option_match and bucket is stem_lines:
            options.append({"key": option_match.group(1).upper(), "text": option_match.group(2)})
            continue
        bucket.append(line)

    stem = "\n".join(stem_lines).strip()
    if len(stem) < 10 or len(stem) > 2000:
        return None
    answer = "\n".join(answer_lines).strip()
    analysis = "\n".join(analysis_lines).strip()
    qtype = "single_choice" if len(options) >= 2 else _infer_type(stem)
    return ExtractedQuestion(
        stem=stem,
        answer=answer,
        analysis=analysis,
        qtype=qtype,
        options=options,
        page=page,
        kp_names=kp_names,
    )


def _infer_type(stem: str) -> str:
    if "证明" in stem:
        return "proof"
    if "____" in stem or "＿＿" in stem:
        return "fill_blank"
    return "calculation"


def question_document(
    question: ExtractedQuestion,
    *,
    tenant_id: str,
    job_id: str,
    subject: str,
    stage: str,
    filename: str,
    kp_ids: list[str],
) -> dict[str, Any]:
    from app.domain.question_rules import simhash64
    from app.shared.time import utcnow

    now = utcnow()
    return {
        "_id": _stable_id("q_", tenant_id, job_id, str(question.page), question.stem[:120]),
        "tenant_id": tenant_id,
        "job_id": job_id,
        "book_id": f"book_{job_id}",
        "source_type": "textbook",
        "source": f"{filename} · 第 {question.page} 页",
        "source_ref": f"{filename} p{question.page}",
        "subject": subject,
        "stage": stage,
        "type": question.qtype,
        "stem": question.stem,
        "options": question.options,
        "answer": question.answer,
        "analysis": question.analysis,
        "knowledge_point_ids": kp_ids,
        "knowledge_points": question.kp_names,
        "difficulty": question.difficulty,
        "review_status": "reviewing",
        "license_status": "pending",
        "quality_score": 0.5,
        "simhash": simhash64(question.stem),
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
