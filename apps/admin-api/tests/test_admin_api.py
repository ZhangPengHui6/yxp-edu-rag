from __future__ import annotations

from httpx import AsyncClient


async def test_textbook_list_and_license_update(
    client: AsyncClient, editor: dict[str, str]
) -> None:
    listing = await client.get("/admin/v1/textbooks", headers=editor)
    assert listing.status_code == 200
    data = listing.json()["data"]
    assert data["total"] == 4
    statuses = {item["license"]["status"] for item in data["items"]}
    assert {"valid", "expiring"} <= statuses

    updated = await client.patch(
        "/admin/v1/textbooks/tb_physics_high_1/license",
        headers=editor,
        json={
            "holder": "上海优学培",
            "scope": "内部教研使用",
            "expiresAt": "2028-12-31",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["status"] == "valid"

    detail = await client.get("/admin/v1/textbooks/tb_physics_high_1", headers=editor)
    assert detail.json()["data"]["license"]["expiresAt"] == "2028-12-31"
    assert len(detail.json()["data"]["chapters"]) == 4


async def test_question_views_and_duplicates(
    client: AsyncClient, editor: dict[str, str]
) -> None:
    listing = await client.get("/admin/v1/questions?pageSize=100", headers=editor)
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 15

    reviewing = await client.get("/admin/v1/questions?status=reviewing", headers=editor)
    ids = {item["id"] for item in reviewing.json()["data"]["items"]}
    assert {"q_math_003", "q_math_004", "q_math_006", "q_phys_003"} <= ids

    duplicates = await client.get("/admin/v1/questions/duplicates", headers=editor)
    assert duplicates.status_code == 200, duplicates.text
    pairs = duplicates.json()["data"]
    assert pairs
    assert {pairs[0]["left"]["id"], pairs[0]["right"]["id"]} == {
        "q_math_001",
        "q_math_008",
    }

    variants = await client.get("/admin/v1/questions/q_math_001/variants", headers=editor)
    assert len(variants.json()["data"]) == 1


async def test_question_update_links_knowledge_point(
    client: AsyncClient, editor: dict[str, str]
) -> None:
    updated = await client.patch(
        "/admin/v1/questions/q_math_006",
        headers=editor,
        json={
            "knowledgePoints": ["空间向量"],
            "difficulty": "难",
            "status": "reviewing",
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()["data"]
    assert body["knowledgePoints"] == ["空间向量"]
    assert body["difficulty"] == "难"
    assert body["status"] == "reviewing"

    reviews = await client.get("/admin/v1/review/tasks", headers=editor)
    assert reviews.json()["data"]["total"] == 6

    missing = await client.patch(
        "/admin/v1/questions/q_math_006",
        headers=editor,
        json={"knowledgePoints": ["不存在的知识点"]},
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["details"]["missing"] == ["不存在的知识点"]


async def test_knowledge_tree_detail_and_merge(
    client: AsyncClient, editor: dict[str, str]
) -> None:
    tree = await client.get("/admin/v1/knowledge-points/tree", headers=editor)
    assert tree.status_code == 200
    roots = tree.json()["data"]
    assert [root["name"] for root in roots] == ["高中数学", "高中物理"]
    assert any(child["name"] == "函数与导数" for child in roots[0]["children"])

    detail = await client.get("/admin/v1/knowledge-points/kp_m_func_prop", headers=editor)
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["code"] == "MATH-FUNC-01"
    assert data["evidences"]
    assert {item["type"] for item in data["relations"]} == {"前置", "后继"}

    coverage = await client.get("/admin/v1/knowledge-points/coverage", headers=editor)
    assert coverage.status_code == 200
    assert coverage.json()["data"]["total"] > 0

    merged = await client.post(
        "/admin/v1/knowledge-points/kp_m_seq_sum/merge",
        headers=editor,
        json={"targetId": "kp_m_seq_geo"},
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["data"]["movedQuestions"] == 1

    detail_target = await client.get(
        "/admin/v1/knowledge-points/kp_m_seq_geo", headers=editor
    )
    assert detail_target.json()["data"]["node"]["questionCount"] >= 1

    after = await client.get("/admin/v1/knowledge-points/tree", headers=editor)
    names = {
        child["name"]
        for module in after.json()["data"][0]["children"]
        for child in module["children"] or []
    }
    assert "数列求和" not in names


async def test_annotation_flow(client: AsyncClient, annotator: dict[str, str]) -> None:
    tasks = await client.get("/admin/v1/annotation/tasks", headers=annotator)
    assert tasks.status_code == 200, tasks.text
    rows = tasks.json()["data"]["items"]
    assert rows[0]["id"] == "ant_001"
    assert rows[0]["pending"] == 3

    detail = await client.get("/admin/v1/annotation/tasks/ant_001", headers=annotator)
    assert len(detail.json()["data"]["blocks"]) == 4

    resolved = await client.post(
        "/admin/v1/annotation/tasks/ant_001/resolve",
        headers=annotator,
        json={"blockId": "blk_ant1_2", "action": "resolve", "content": "校对后的正文"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["data"] == {"resolved": 2, "total": 4}


async def test_review_decision(client: AsyncClient, reviewer: dict[str, str]) -> None:
    tasks = await client.get("/admin/v1/review/tasks", headers=reviewer)
    assert tasks.status_code == 200, tasks.text
    assert tasks.json()["data"]["total"] == 5
    assert tasks.json()["data"]["items"][0]["priority"] == "high"

    decision = await client.post(
        "/admin/v1/review/tasks/rev_001/decision",
        headers=reviewer,
        json={"decision": "approve", "comment": "解析完整，同意。"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["data"]["decision"] == "approve"

    after = await client.get("/admin/v1/review/tasks", headers=reviewer)
    assert after.json()["data"]["total"] == 4

    question = await client.get("/admin/v1/questions/q_math_003", headers=reviewer)
    assert question.json()["data"]["status"] == "reviewing"

    again = await client.post(
        "/admin/v1/review/tasks/rev_001/decision",
        headers=reviewer,
        json={"decision": "approve"},
    )
    assert again.status_code == 409
    assert again.json()["code"] == 40902


async def test_publish_stage_commit_rollback(
    client: AsyncClient, editor: dict[str, str]
) -> None:
    created = await client.post(
        "/admin/v1/publish/orders",
        headers=editor,
        json={"target": "production"},
    )
    assert created.status_code == 200, created.text
    order = created.json()["data"]
    assert order["status"] == "draft"
    assert len(order["checks"]) == 4

    staged = await client.post(
        f"/admin/v1/publish/orders/{order['id']}/stage", headers=editor
    )
    assert staged.status_code == 200, staged.text
    assert staged.json()["data"]["status"] == "staged"

    committed = await client.post(
        f"/admin/v1/publish/orders/{order['id']}/commit", headers=editor
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["data"]["status"] == "published"

    published_question = await client.get(
        "/admin/v1/questions/q_math_006", headers=editor
    )
    assert published_question.json()["data"]["status"] == "published"

    rolled_back = await client.post(
        f"/admin/v1/publish/orders/{order['id']}/rollback", headers=editor
    )
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["data"]["status"] == "rolled_back"


async def test_evaluation_run_and_report(client: AsyncClient, editor: dict[str, str]) -> None:
    datasets = await client.get("/admin/v1/evaluation/datasets", headers=editor)
    assert datasets.json()["data"]
    assert datasets.json()["data"][0]["questionCount"] == 4

    run = await client.post(
        "/admin/v1/evaluation/runs",
        headers=editor,
        json={"dataset": "高中数学核心题集 · 4 题", "model": "qwen2.5-vl-7b-instruct"},
    )
    assert run.status_code == 200, run.text
    body = run.json()["data"]
    assert body["status"] == "passed"
    assert body["recall"] >= 0.85

    report = await client.get(f"/admin/v1/evaluation/runs/{body['id']}", headers=editor)
    assert report.status_code == 200
    assert len(report.json()["data"]["metrics"]) >= 3


async def test_dashboard(client: AsyncClient, editor: dict[str, str]) -> None:
    summary = await client.get("/admin/v1/dashboard/summary", headers=editor)
    assert summary.status_code == 200
    data = summary.json()["data"]
    assert len(data["metrics"]) == 4
    assert len(data["todos"]) == 4
    assert len(data["trend"]) == 7
    assert int(data["metrics"][0]["value"]) >= 2

    todos = await client.get("/admin/v1/dashboard/todos", headers=editor)
    assert len(todos.json()["data"]) == 4


async def test_tenant_scope(client: AsyncClient) -> None:
    token = (
        await client.post(
            "/admin/v1/auth/login", json={"username": "zhaomin", "password": "123456"}
        )
    ).json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}
    summary = await client.get("/admin/v1/dashboard/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["data"]["metrics"][2]["note"].startswith("个资料任务")

    forbidden = await client.get("/admin/v1/iam/users", headers=headers)
    assert forbidden.status_code == 403
