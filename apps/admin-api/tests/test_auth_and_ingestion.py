from __future__ import annotations

import asyncio

from httpx import AsyncClient


async def test_login_profile_and_logout(client: AsyncClient) -> None:
    response = await client.post(
        "/admin/v1/auth/login", json={"username": "admin", "password": "123456"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["user"]["role"] == "super_admin"
    assert len(body["data"]["user"]["permissions"]) == 37
    assert body["request_id"].startswith("req_")

    token = body["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}
    profile = await client.get("/admin/v1/auth/profile", headers=headers)
    assert profile.json()["data"]["username"] == "admin"

    logout = await client.post("/admin/v1/auth/logout", headers=headers)
    assert logout.json()["code"] == 0


async def test_bad_credentials_and_validation(client: AsyncClient) -> None:
    response = await client.post(
        "/admin/v1/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == 40101
    assert response.json()["error"]["code"] == "UNAUTHORIZED"

    invalid = await client.post("/admin/v1/auth/login", json={"username": "admin"})
    assert invalid.status_code == 400
    assert invalid.json()["code"] == 40001


async def test_missing_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/admin/v1/dashboard/summary")
    assert response.status_code == 401
    assert response.json()["code"] == 40101


async def test_rbac_rejects_annotator_from_iam(client: AsyncClient, annotator: dict[str, str]) -> None:
    forbidden = await client.get("/admin/v1/iam/users", headers=annotator)
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == 40301
    assert forbidden.json()["error"]["details"]["required"] == "iam.user"

    allowed = await client.get("/admin/v1/questions", headers=annotator)
    assert allowed.status_code == 200


async def test_iam_read_models(client: AsyncClient, admin: dict[str, str]) -> None:
    users = await client.get("/admin/v1/iam/users", headers=admin)
    assert users.status_code == 200
    assert users.json()["data"]["total"] == 6
    assert "pageSize" in users.json()["data"]

    roles = await client.get("/admin/v1/iam/roles", headers=admin)
    assert {row["code"] for row in roles.json()["data"]} == {
        "super_admin",
        "kb_admin",
        "annotator",
        "reviewer",
        "publisher",
        "viewer",
    }
    super_admin = next(row for row in roles.json()["data"] if row["code"] == "super_admin")
    assert super_admin["permissionCount"] == 37

    tenants = await client.get("/admin/v1/iam/tenants", headers=admin)
    assert tenants.json()["data"]["total"] == 4

    dicts = await client.get("/admin/v1/iam/dicts", headers=admin)
    assert "questionType" in dicts.json()["data"]

    audit = await client.get("/admin/v1/iam/audit", headers=admin)
    assert audit.json()["data"]["total"] >= 8


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/admin/v1/health")
    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert "vector" in response.json()["data"]


async def test_ingestion_pipeline_reaches_succeeded(
    client: AsyncClient, editor: dict[str, str]
) -> None:
    created = await client.post(
        "/admin/v1/ingestion/jobs",
        headers=editor,
        json={
            "filename": "新教材.pdf",
            "objectKey": "uploads/2026/10/new.pdf",
            "sourceType": "PDF",
            "subject": "数学",
            "stage": "high",
            "pages": 40,
            "sizeMb": 8.2,
        },
    )
    assert created.status_code == 200, created.text
    job = created.json()["data"]
    assert len(job["steps"]) == 5
    assert job["status"] in {"pending", "running", "succeeded"}

    final = job
    for _ in range(100):
        response = await client.get(f"/admin/v1/ingestion/jobs/{job['id']}", headers=editor)
        final = response.json()["data"]
        if final["status"] in {"succeeded", "failed"}:
            break
        await asyncio.sleep(0.05)

    assert final["status"] == "succeeded"
    assert final["progress"] == 100
    assert all(step["status"] == "done" for step in final["steps"])


async def test_ingestion_retry_and_cancel(client: AsyncClient, editor: dict[str, str]) -> None:
    retried = await client.post("/admin/v1/ingestion/jobs/job_physics_lab/retry", headers=editor)
    assert retried.status_code == 200, retried.text
    assert retried.json()["data"]["status"] in {"pending", "running"}

    for _ in range(100):
        response = await client.get("/admin/v1/ingestion/jobs/job_physics_lab", headers=editor)
        if response.json()["data"]["status"] == "succeeded":
            break
        await asyncio.sleep(0.05)
    assert response.json()["data"]["status"] == "succeeded"

    created = await client.post(
        "/admin/v1/ingestion/jobs",
        headers=editor,
        json={
            "filename": "待终止.pdf",
            "objectKey": "uploads/2026/10/cancel.pdf",
            "pages": 10,
            "sizeMb": 1.0,
        },
    )
    job_id = created.json()["data"]["id"]
    canceled = await client.post(f"/admin/v1/ingestion/jobs/{job_id}/cancel", headers=editor)
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["data"]["status"] == "canceled"
    await asyncio.sleep(0.1)

    succeeded = seeded = await client.get(
        "/admin/v1/ingestion/jobs/job_math", headers=editor
    )
    assert seeded is not None
    retry_succeeded = await client.post(
        "/admin/v1/ingestion/jobs/job_math/retry", headers=editor
    )
    assert retry_succeeded.status_code == 409
    assert retry_succeeded.json()["code"] == 40902
    assert succeeded.status_code == 200
