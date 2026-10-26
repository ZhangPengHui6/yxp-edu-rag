from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.core.envelope import Envelope, ok
from app.infra.pg import get_session
from app.modules.iam import service as iam
from app.modules.iam.schemas import LoginIn, LoginResult, UserOut
from app.shared.time import utcnow

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Envelope[LoginResult])
async def login(payload: LoginIn, session: AsyncSession = Depends(get_session)):
    principal = await iam.authenticate(session, payload.username, payload.password)
    token, expires_in = iam.issue_token(principal)
    await iam.touch_login(session, principal.id)
    principal.last_login_at = utcnow()
    return ok(
        LoginResult(
            token=token,
            user=iam.principal_to_user(principal),
            expires_in=expires_in,
        )
    )


@router.post("/logout", response_model=Envelope[None])
async def logout(response: Response, _: CurrentUser = Depends(get_current_user)):
    # JWT is stateless in the POC; production revokes the jti in Redis.
    response.headers["X-Logout"] = "1"
    return ok(None, "已退出登录")


@router.get("/profile", response_model=Envelope[UserOut])
async def profile(user: CurrentUser = Depends(get_current_user)):
    return ok(
        UserOut(
            id=user.id,
            username=user.username,
            name=user.name,
            role=user.role_code,
            role_name=user.role_name,
            permissions=sorted(user.permissions),
            tenant=user.tenant_name,
            last_login_at=user.last_login_at.strftime("%Y-%m-%d %H:%M")
            if user.last_login_at
            else "-",
        )
    )
