"""Test bootstrap: isolated SQLite + mongomock, no external services."""

from __future__ import annotations

import os

os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///./data/test_admin.db",
        "MONGO_URL": "",
        "MONGO_DB": "yxp_test",
        "JWT_SECRET": "test-secret-0123456789abcdef0123456789abcdef",
        "OBJECT_STORAGE": "local",
        "UPLOAD_DIR": "./data/test_uploads",
        "TASK_MODE": "inline",
        "OCR_PROVIDER": "placeholder",
        "EMBEDDING_PROVIDER": "off",
        "PIPELINE_STEP_DELAY": "0.05",
        "SEED_ON_START": "false",
        "LOG_LEVEL": "WARNING",
    }
)

from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.infra import mongo, pg  # noqa: E402
from app.infra.chunk_store import get_chunk_store  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.seed import bootstrap_vector_aliases, seed_all  # noqa: E402

MONGO_COLLECTIONS = (
    "tenants",
    "textbooks",
    "chapters",
    "licenses",
    "questions",
    "question_variants",
    "knowledge_points",
    "kp_relations",
    "annotation_blocks",
    "api_keys",
)


async def _reset_mongo() -> None:
    for name in MONGO_COLLECTIONS:
        await mongo.col(name).delete_many({})


async def _reset_state() -> None:
    await pg.init_models()
    async with pg.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await _reset_mongo()
    get_chunk_store().clear()
    await seed_all()
    await bootstrap_vector_aliases()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    await _reset_state()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def login(client: AsyncClient, username: str = "admin") -> str:
    response = await client.post(
        "/admin/v1/auth/login", json={"username": username, "password": "123456"}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["token"]


async def auth_headers(client: AsyncClient, username: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {await login(client, username)}"}


@pytest.fixture
async def admin(client: AsyncClient) -> dict[str, str]:
    return await auth_headers(client, "admin")


@pytest.fixture
async def editor(client: AsyncClient) -> dict[str, str]:
    return await auth_headers(client, "zhangwei")


@pytest.fixture
async def reviewer(client: AsyncClient) -> dict[str, str]:
    return await auth_headers(client, "liyuan")


@pytest.fixture
async def annotator(client: AsyncClient) -> dict[str, str]:
    return await auth_headers(client, "chenxi")
