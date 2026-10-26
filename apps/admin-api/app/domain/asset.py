"""Asset / workflow state machines (数据对象 §9). Pure Python, no I/O."""

from __future__ import annotations

from app.core.errors import InvalidState

ASSET_STATES = [
    "draft",
    "parsing",
    "parsed",
    "annotated",
    "reviewing",
    "approved",
    "published",
    "deprecated",
]

ASSET_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"parsing", "deprecated"},
    "parsing": {"parsed", "draft", "deprecated"},
    "parsed": {"annotated", "reviewing", "deprecated"},
    "annotated": {"reviewing", "deprecated"},
    "reviewing": {"approved", "parsed", "deprecated"},
    "approved": {"published", "reviewing", "deprecated"},
    "published": {"deprecated", "reviewing"},
    "deprecated": {"draft"},
}

JOB_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "canceled"},
    "running": {"succeeded", "failed", "canceled"},
    "failed": {"pending"},
    "succeeded": set(),
    "canceled": set(),
}

REVIEW_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in_review", "approved", "rejected", "transferred"},
    "in_review": {"approved", "rejected", "transferred"},
    "approved": set(),
    "rejected": set(),
    "transferred": set(),
}

PUBLISH_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"staged", "failed"},
    "staged": {"published", "failed"},
    "published": {"rolled_back"},
    "failed": {"draft"},
    "rolled_back": {"draft"},
}


def _assert(state: str, allowed: dict[str, set[str]], target: str, label: str) -> None:
    if target not in allowed.get(state, set()):
        raise InvalidState(f"{label}不能从 {state} 变更为 {target}", {"from": state, "to": target})


def assert_asset_transition(current: str, target: str) -> None:
    _assert(current, ASSET_TRANSITIONS, target, "资产状态")


def assert_job_transition(current: str, target: str) -> None:
    _assert(current, JOB_TRANSITIONS, target, "任务状态")


def assert_review_transition(current: str, target: str) -> None:
    _assert(current, REVIEW_TRANSITIONS, target, "审核状态")


def assert_publish_transition(current: str, target: str) -> None:
    _assert(current, PUBLISH_TRANSITIONS, target, "发布单状态")


def can_retry_job(status: str) -> bool:
    return status in {"failed", "canceled"}


def can_cancel_job(status: str) -> bool:
    return status in {"pending", "running"}
