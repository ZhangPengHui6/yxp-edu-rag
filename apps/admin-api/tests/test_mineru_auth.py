"""MinerU auth: static token and OpenXLab AK/SK -> JWT exchange."""

from __future__ import annotations

import base64
import hmac
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.infra.providers import mineru
from app.infra.providers.mineru import (
    CHINA_TZ,
    MineruOcrProvider,
    _auth_payload,
    _parse_auth_time,
)

AUTH_URL = "https://openapi.example/api/v1/open"


class _Response:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _Client:
    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, url: str, json: dict[str, str]) -> _Response:
        self.calls.append((url, json))
        return _Response(self._responses[url.rsplit("/", 1)[-1]])


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> MineruOcrProvider:
    monkeypatch.setattr(settings, "mineru_token", "")
    monkeypatch.setattr(settings, "mineru_access_key_id", "ak_test")
    monkeypatch.setattr(settings, "mineru_secret_access_key", "sk_test")
    monkeypatch.setattr(settings, "mineru_auth_url", AUTH_URL)
    return MineruOcrProvider()


def _envelope(data: dict[str, Any]) -> dict[str, Any]:
    return {"msgCode": "10000", "msg": "ok", "data": {"msgCode": "10000", "data": data}}


def test_static_token_is_used_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mineru_token", "plain-token")
    p = MineruOcrProvider()
    assert p._authorization() == "Bearer plain-token"


def test_login_signs_nonce_and_caches_jwt(
    provider: MineruOcrProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _Client(
        {
            "auth": _envelope({"nonce": "nonce-1", "algorithm": "HmacSHA1"}),
            "getJwt": _envelope(
                {
                    "jwt": "Bearer jwt-1",
                    "expiration": "2099-01-01 00:00:00",
                    "refresh_token": "refresh-1",
                    "refresh_expiration": "2099-01-08 00:00:00",
                }
            ),
        }
    )
    monkeypatch.setattr(mineru.httpx, "Client", lambda **_: client)

    assert provider._authorization() == "Bearer jwt-1"
    expected = base64.b64encode(
        hmac.new(b"sk_test", b"nonce-1", "SHA1").digest()
    ).decode()
    assert [url.rsplit("/", 1)[-1] for url, _ in client.calls] == ["auth", "getJwt"]
    assert client.calls[0][1] == {"ak": "ak_test"}
    assert client.calls[1][1] == {"ak": "ak_test", "d": expected}
    assert provider._refresh_token == "refresh-1"


def test_valid_jwt_is_reused_without_new_auth(
    provider: MineruOcrProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    expires = datetime.now(CHINA_TZ) + timedelta(hours=1)
    provider._jwt = "Bearer cached"
    provider._jwt_expiration = expires.strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(
        provider, "_login", lambda: pytest.fail("should not login")
    )
    assert provider._authorization() == "Bearer cached"


def test_expired_jwt_refreshes_when_refresh_token_is_valid(
    provider: MineruOcrProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider._jwt = "Bearer old"
    provider._jwt_expiration = "2000-01-01 00:00:00"
    provider._refresh_token = "refresh-1"
    provider._refresh_expiration = (
        datetime.now(CHINA_TZ) + timedelta(days=1)
    ).strftime("%Y-%m-%d %H:%M:%S")

    def refresh() -> None:
        provider._jwt = "Bearer refreshed"
        provider._jwt_expiration = "2099-01-01 00:00:00"

    monkeypatch.setattr(provider, "_refresh_jwt", refresh)
    monkeypatch.setattr(provider, "_login", lambda: pytest.fail("should not login"))
    assert provider._authorization() == "Bearer refreshed"


def test_expired_refresh_falls_back_to_login(
    provider: MineruOcrProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider._jwt = "Bearer old"
    provider._jwt_expiration = "2000-01-01 00:00:00"
    provider._refresh_token = "refresh-1"
    provider._refresh_expiration = "2000-01-08 00:00:00"

    def login() -> None:
        provider._jwt = "Bearer relogin"
        provider._jwt_expiration = "2099-01-01 00:00:00"

    monkeypatch.setattr(provider, "_login", login)
    assert provider._authorization() == "Bearer relogin"


def test_failed_refresh_falls_back_to_login(
    provider: MineruOcrProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider._jwt = "Bearer old"
    provider._jwt_expiration = "2000-01-01 00:00:00"
    provider._refresh_token = "refresh-1"
    provider._refresh_expiration = "2099-01-08 00:00:00"

    def fail() -> None:
        raise ProviderUnavailable("refresh expired")

    def login() -> None:
        provider._jwt = "Bearer relogin"
        provider._jwt_expiration = "2099-01-01 00:00:00"

    monkeypatch.setattr(provider, "_refresh_jwt", fail)
    monkeypatch.setattr(provider, "_login", login)
    assert provider._authorization() == "Bearer relogin"


def test_auth_payload_rejects_error_code() -> None:
    with pytest.raises(ProviderUnavailable):
        _auth_payload({"msgCode": "10001", "msg": "bad ak"}, "auth")
    with pytest.raises(ProviderUnavailable):
        _auth_payload({"msgCode": "10000", "data": {"msgCode": "10001"}}, "getJwt")
    assert _auth_payload(_envelope({"nonce": "n"}), "auth") == {"nonce": "n"}


def test_parse_auth_time_uses_china_timezone() -> None:
    parsed = _parse_auth_time("2026-10-24 16:48:42")
    assert parsed.utcoffset() == timedelta(hours=8)
    assert _parse_auth_time("") == datetime.min.replace(tzinfo=CHINA_TZ)
