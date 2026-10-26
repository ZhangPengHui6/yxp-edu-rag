from __future__ import annotations

from pydantic import Field

from app.core.schemas import CamelModel


class LoginIn(CamelModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserOut(CamelModel):
    id: str
    username: str
    name: str
    role: str
    role_name: str
    permissions: list[str]
    tenant: str
    last_login_at: str


class LoginResult(CamelModel):
    token: str
    user: UserOut
    expires_in: int


class RoleRowOut(CamelModel):
    code: str
    name: str
    description: str
    user_count: int
    permission_count: int


class TenantOut(CamelModel):
    id: str
    name: str
    plan: str
    users: int
    status: str
