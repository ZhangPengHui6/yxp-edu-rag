"""Publish-order presentation and readiness rules. Pure functions."""

from __future__ import annotations

from typing import Any

PUBLISH_STEPS = [
    ("stage", "暂存 Stage", "写入新索引集合，不改动线上别名"),
    ("commit", "提交 Commit", "校验通过后切换别名并生效"),
    ("done", "完成", "线上服务已切换，可观察后回滚"),
]


def build_stages(status: str, *, vector_count: int = 0, target_alias: str = "") -> list[dict]:
    if status == "draft":
        statuses = ["pending", "pending", "pending"]
        descriptions = [
            "尚未执行暂存",
            PUBLISH_STEPS[1][2],
            PUBLISH_STEPS[2][2],
        ]
    elif status == "staged":
        statuses = ["done", "running", "pending"]
        descriptions = [
            f"新索引已写入 {vector_count} 条向量",
            "等待提交并切换别名",
            PUBLISH_STEPS[2][2],
        ]
    elif status == "published":
        statuses = ["done", "done", "done"]
        descriptions = [
            f"新索引已写入 {vector_count} 条向量",
            "别名已切换",
            "线上服务已切换",
        ]
    elif status == "rolled_back":
        statuses = ["done", "done", "pending"]
        descriptions = [
            f"新索引已写入 {vector_count} 条向量",
            "已回滚至上一版本",
            f"当前线上为 {target_alias or '上一版本'}",
        ]
    else:  # failed
        statuses = ["done", "failed", "pending"]
        descriptions = [
            "暂存结果保留，便于排查",
            "提交失败，未切换线上别名",
            "修复后可重新创建发布单",
        ]
    return [
        {"key": key, "title": title, "description": description, "status": stage_status}
        for (key, title, _), stage_status, description in zip(
            PUBLISH_STEPS, statuses, descriptions, strict=True
        )
    ]


def build_checks(
    *,
    reference_ok: bool,
    reference_detail: str,
    vector_ok: bool,
    vector_detail: str,
    alias_ok: bool,
    alias_detail: str,
    eval_ok: bool,
    eval_detail: str,
) -> list[dict[str, Any]]:
    return [
        {"label": "引用完整性", "detail": reference_detail, "passed": reference_ok},
        {"label": "向量索引", "detail": vector_detail, "passed": vector_ok},
        {"label": "别名可用性", "detail": alias_detail, "passed": alias_ok},
        {"label": "抽样评测", "detail": eval_detail, "passed": eval_ok},
    ]


def all_checks_passed(checks: list[dict[str, Any]]) -> bool:
    return all(bool(item.get("passed")) for item in checks)


def short_version(version: str) -> str:
    return version.replace("chunks_", "").replace("questions_", "")
