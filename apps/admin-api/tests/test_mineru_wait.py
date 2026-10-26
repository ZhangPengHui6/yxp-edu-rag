"""MinerU polling: tolerate the transient ``task not found or expire`` reply."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.infra.providers import mineru
from app.infra.providers.mineru import MineruOcrProvider


class _Response:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _PollClient:
    def __init__(self, bodies: list[dict[str, Any]]) -> None:
        self._bodies = bodies
        self.calls = 0

    def __enter__(self) -> _PollClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str, headers: dict[str, str]) -> _Response:
        body = self._bodies[min(self.calls, len(self._bodies) - 1)]
        self.calls += 1
        return _Response(body)


def _not_found() -> dict[str, Any]:
    return {"code": -60012, "msg": "task not found or expire"}


def _pending() -> dict[str, Any]:
    return {"code": 0, "data": {"extract_result": [{"state": "running"}]}}


def _done(url: str = "https://oss.example/result.zip") -> dict[str, Any]:
    return {"code": 0, "data": {"extract_result": [{"state": "done", "full_zip_url": url}]}}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> MineruOcrProvider:
    monkeypatch.setattr(settings, "mineru_token", "plain-token")
    monkeypatch.setattr(settings, "ocr_poll_seconds", 1)
    return MineruOcrProvider()


def _fake_clock(monkeypatch: pytest.MonkeyPatch, start: float = 0.0) -> list[float]:
    now = [start]
    monkeypatch.setattr(mineru.time, "time", lambda: now[0])
    monkeypatch.setattr(mineru.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now


def test_not_found_is_retried_until_the_batch_appears(
    provider: MineruOcrProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ocr_timeout_seconds", 100)
    _fake_clock(monkeypatch)
    client = _PollClient([_not_found(), _not_found(), _pending(), _done()])
    monkeypatch.setattr(mineru.httpx, "Client", lambda **_: client)

    assert provider._wait("batch-1") == "https://oss.example/result.zip"
    assert client.calls == 4


def test_not_found_persisting_past_the_grace_period_fails(
    provider: MineruOcrProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ocr_timeout_seconds", 10_000)
    monkeypatch.setattr(settings, "ocr_poll_seconds", 60)
    _fake_clock(monkeypatch)
    client = _PollClient([_not_found()])
    monkeypatch.setattr(mineru.httpx, "Client", lambda **_: client)

    with pytest.raises(ProviderUnavailable, match="-60012|not found"):
        provider._wait("batch-1")
    assert client.calls == 5  # 0s, 60s, 120s, 180s, 240s -> beyond the 180s grace
