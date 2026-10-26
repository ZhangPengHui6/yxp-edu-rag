from __future__ import annotations

import pytest

from app.core.errors import InvalidState
from app.core.permissions import ALL_CODES, ROLES, permissions_of
from app.domain.asset import (
    assert_asset_transition,
    assert_job_transition,
    assert_publish_transition,
    can_cancel_job,
    can_retry_job,
)
from app.domain.publish_rules import all_checks_passed, build_checks, build_stages
from app.domain.question_rules import find_duplicate_pairs, quality_gate, status_label
from app.shared.text import hamming64, simhash64


def test_permission_matrix_is_consistent() -> None:
    assert len(ALL_CODES) == 37
    assert len(set(ALL_CODES)) == 37
    assert len(ROLES) == 6
    assert len(permissions_of("super_admin")) == 37
    assert "*" not in permissions_of("super_admin")
    for role in ROLES:
        if role["code"] != "super_admin":
            assert set(role["permissions"]) <= set(ALL_CODES)


def test_asset_state_machine() -> None:
    assert_asset_transition("draft", "parsing")
    assert_asset_transition("reviewing", "approved")
    assert_asset_transition("approved", "published")
    with pytest.raises(InvalidState):
        assert_asset_transition("draft", "published")
    with pytest.raises(InvalidState):
        assert_asset_transition("published", "approved")


def test_job_state_machine() -> None:
    assert_job_transition("pending", "running")
    assert_job_transition("running", "failed")
    with pytest.raises(InvalidState):
        assert_job_transition("succeeded", "running")
    assert can_retry_job("failed")
    assert not can_retry_job("succeeded")
    assert can_cancel_job("running")
    assert not can_cancel_job("canceled")


def test_publish_state_machine_and_rules() -> None:
    assert_publish_transition("draft", "staged")
    assert_publish_transition("staged", "published")
    assert_publish_transition("published", "rolled_back")
    with pytest.raises(InvalidState):
        assert_publish_transition("draft", "published")

    checks = build_checks(
        reference_ok=True,
        reference_detail="ok",
        vector_ok=True,
        vector_detail="ok",
        alias_ok=False,
        alias_detail="broken",
        eval_ok=True,
        eval_detail="ok",
    )
    assert not all_checks_passed(checks)
    assert build_stages("draft")[0]["status"] == "pending"
    assert [stage["status"] for stage in build_stages("published")] == [
        "done",
        "done",
        "done",
    ]


def test_simhash_and_duplicate_detection() -> None:
    left = "函数 f(x)=x³-3x 的单调递增区间是（ ）。"
    right = "函数 f（x）＝x³－3x 的单调递增区间是（ ）"
    assert hamming64(simhash64(left), simhash64(right)) <= 6

    questions = [
        {"_id": "q1", "stem": left, "review_status": "published"},
        {"_id": "q2", "stem": right, "review_status": "draft"},
        {"_id": "q3", "stem": "完全不同的题目：求 1+1。", "review_status": "published"},
    ]
    pairs = find_duplicate_pairs(questions)
    assert pairs
    assert {pairs[0]["left"]["_id"], pairs[0]["right"]["_id"]} == {"q1", "q2"}
    assert pairs[0]["score"] >= 0.8


def test_quality_gate_and_status_mapping() -> None:
    good = {
        "stem": "题干",
        "answer": "答案",
        "knowledge_point_ids": ["kp"],
        "quality_score": 0.8,
        "review_status": "approved",
    }
    assert quality_gate(good) == []
    assert "尚未通过审核" in quality_gate({**good, "review_status": "draft"})
    # approved-but-not-published is displayed as reviewing by the admin API.
    assert status_label("approved") == "reviewing"
    assert status_label("deprecated") == "archived"
