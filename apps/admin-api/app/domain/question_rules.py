"""Question normalisation, labels, quality gate and duplicate candidates."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.shared.text import hamming64, normalize_stem, simhash64

TYPE_TO_LABEL = {
    "single_choice": "选择题",
    "fill_blank": "填空题",
    "calculation": "解答题",
}
LABEL_TO_TYPE = {label: key for key, label in TYPE_TO_LABEL.items()}
DIFFICULTY_TO_LABEL = {"easy": "易", "medium": "中", "hard": "难"}
LABEL_TO_DIFFICULTY = {label: key for key, label in DIFFICULTY_TO_LABEL.items()}
REVIEW_TO_API = {
    "draft": "draft",
    "reviewing": "reviewing",
    # 审核通过但尚未随发布单上线：管理端仍视为「审核中」，
    # 避免新增前端不认识的第五种状态。
    "approved": "reviewing",
    "published": "published",
    "deprecated": "archived",
}
API_TO_REVIEW = {
    "draft": "draft",
    "reviewing": "reviewing",
    "published": "published",
    "archived": "deprecated",
}


def type_label(value: str) -> str:
    return TYPE_TO_LABEL.get(value, value)


def type_value(label: str | None) -> str | None:
    if not label:
        return None
    return LABEL_TO_TYPE.get(label, label)


def difficulty_label(value: str) -> str:
    return DIFFICULTY_TO_LABEL.get(value, value)


def difficulty_value(label: str | None) -> str | None:
    if not label:
        return None
    return LABEL_TO_DIFFICULTY.get(label, label)


def status_label(value: str) -> str:
    return REVIEW_TO_API.get(value, value)


def status_value(label: str | None) -> str | None:
    if not label:
        return None
    return API_TO_REVIEW.get(label, label)


def quality_gate(question: dict[str, Any]) -> list[str]:
    """Return blocking reasons; empty means publishable."""
    reasons: list[str] = []
    if not (question.get("stem") or "").strip():
        reasons.append("缺少题干")
    if not (question.get("answer") or "").strip():
        reasons.append("缺少答案")
    if not question.get("knowledge_point_ids"):
        reasons.append("未关联知识点")
    if float(question.get("quality_score") or 0) < 0.6:
        reasons.append("质量分低于 0.6")
    if question.get("review_status") != "approved" and question.get("review_status") != "published":
        reasons.append("尚未通过审核")
    return reasons


def find_duplicate_pairs(
    questions: Iterable[dict[str, Any]], *, max_distance: int = 3, limit: int = 20
) -> list[dict[str, Any]]:
    """Comparable pair candidates ordered by descending similarity."""
    items = [q for q in questions if q.get("stem")]
    pairs: list[dict[str, Any]] = []
    for i, left in enumerate(items):
        left_hash = left.get("simhash") or simhash64(left["stem"])
        left_norm = normalize_stem(left["stem"])
        for right in items[i + 1 :]:
            right_hash = right.get("simhash") or simhash64(right["stem"])
            distance = hamming64(left_hash, right_hash)
            exact = left_norm == normalize_stem(right["stem"])
            if exact or distance <= max_distance:
                score = 1.0 if exact else max(0.0, 1 - distance / 16)
                pairs.append(
                    {
                        "id": f"dup_{left['_id']}_{right['_id']}",
                        "score": round(score, 2),
                        "left": left,
                        "right": right,
                        "suggestion": "建议保留更新时间较新的题目，另一题标记为重复并下线"
                        if score >= 0.9
                        else "建议人工复核，确认是否为同一题的不同表述",
                        "_distance": distance,
                    }
                )
    pairs.sort(key=lambda item: (-item["score"], item["_distance"]))
    return pairs[:limit]
