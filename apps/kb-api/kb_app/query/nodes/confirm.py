"""Query-understanding node: normalize the question and infer scope filters."""

from __future__ import annotations

import re
import time
from typing import Any

from app.core.errors import AppError

from kb_app.config import get_kb_settings
from kb_app.query.state import QueryState

SUBJECT_KEYWORDS = {
    "数学": "数学",
    "物理": "物理",
    "化学": "化学",
    "语文": "语文",
    "英语": "英语",
    "生物": "生物",
    "历史": "历史",
    "地理": "地理",
    "政治": "政治",
}

STAGE_KEYWORDS = {
    "小学": "primary",
    "初中": "junior",
    "高中": "high",
}

_WHITESPACE = re.compile(r"\s+")


def _last_user_message(messages: list[dict[str, str]]) -> str:
    for item in reversed(messages or []):
        if item.get("role") == "user" and (item.get("content") or "").strip():
            return str(item["content"]).strip()
    return ""


def _infer(query: str, mapping: dict[str, str]) -> str:
    for keyword, value in mapping.items():
        if keyword in query:
            return value
    return ""


async def confirm_node(state: QueryState) -> dict[str, Any]:
    """Normalize the question and fill subject/stage from simple keywords.

    This is the K12 analogue of the reference project's "item name confirm"
    node: it makes the retrieval scope explicit before searching, and rejects
    empty questions early instead of spending an embedding call.
    """
    started = time.perf_counter()
    query = (state.get("query") or "").strip() or _last_user_message(
        state.get("messages") or []
    )
    query = _WHITESPACE.sub(" ", query).strip()

    limit = get_kb_settings().kb_max_question_chars
    if len(query) < 2:
        raise AppError("问题不能为空", error_code="INVALID_ARGUMENT")
    if len(query) > limit:
        raise AppError(
            f"问题超过 {limit} 字，请拆分后再提问",
            error_code="INVALID_ARGUMENT",
            details={"length": len(query)},
        )

    subject = (state.get("subject") or "").strip() or _infer(query, SUBJECT_KEYWORDS)
    stage = (state.get("stage") or "").strip() or _infer(query, STAGE_KEYWORDS)

    return {
        "query": query,
        "rewritten_query": query,
        "subject": subject,
        "stage": stage,
        "timings": {
            **(state.get("timings") or {}),
            "confirm": int((time.perf_counter() - started) * 1000),
        },
    }
