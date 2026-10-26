"""Request dependencies: DB session, current user and permission guards."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden, Unauthorized
from app.core.security import decode_access_token
from app.infra.pg import get_session
from app.modules.iam.service import load_principal


@dataclass(slots=True)
class CurrentUser:
    id: str
    tenant_id: str
    tenant_name: str
    username: str
    display_name: str
    role_code: str
    role_name: str
    permissions: set[str] = field(default_factory=set)
    last_login_at: datetime | None = None

    @property
    def name(self) -> str:
        return self.display_name or self.username

    def has(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized()
    return authorization.split(" ", 1)[1].strip()


async def get_current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    token = _bearer(authorization)
    try:
        payload = decode_access_token(token)
    except Exception as exc:  # noqa: BLE001 - all JWT failures are 401
        raise Unauthorized() from exc
    user_id = payload.get("sub")
    if not user_id:
        raise Unauthorized()
    principal = await load_principal(session, user_id)
    if principal is None:
        raise Unauthorized()
    return CurrentUser(
        id=principal.id,
        tenant_id=principal.tenant_id,
        tenant_name=principal.tenant_name,
        username=principal.username,
        display_name=principal.display_name,
        role_code=principal.role_code,
        role_name=principal.role_name,
        permissions=principal.permissions,
        last_login_at=principal.last_login_at,  # type: ignore[arg-type]
    )


def require(permission: str) -> Callable:
    async def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.has(permission):
            raise Forbidden(f"缺少权限：{permission}", {"required": permission})
        return user

    return dependency


def require_any(*permissions: str) -> Callable:
    async def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not any(user.has(permission) for permission in permissions):
            raise Forbidden("缺少访问权限", {"requiredAny": list(permissions)})
        return user

    return dependency
