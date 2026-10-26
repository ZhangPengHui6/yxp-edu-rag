"""Authentication, identity aggregation and IAM read models."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Unauthorized
from app.core.permissions import ALL_CODES, ROLE_BY_CODE
from app.core.security import create_access_token, verify_password
from app.infra.mongo import col
from app.models import Permission, Role, RolePermission, User, UserRole
from app.modules.iam.schemas import RoleRowOut, TenantOut, UserOut
from app.shared.time import fmt_dt, utcnow


@dataclass(slots=True)
class Principal:
    id: str
    tenant_id: str
    tenant_name: str
    username: str
    display_name: str
    role_code: str
    role_name: str
    permissions: set[str] = field(default_factory=set)
    last_login_at: object | None = None


async def tenant_name(tenant_id: str) -> str:
    doc = await col("tenants").find_one({"_id": tenant_id})
    return (doc or {}).get("name") or "默认租户"


async def _role_of(session: AsyncSession, user_id: str) -> Role | None:
    return (
        await session.execute(
            select(Role)
            .join(UserRole, UserRole.role_id == Role.role_id)
            .where(UserRole.user_id == user_id)
            .limit(1)
        )
    ).scalar_one_or_none()


async def _permissions_of(session: AsyncSession, role_id: str) -> set[str]:
    codes = (
        (
            await session.execute(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.permission_id)
                .where(RolePermission.role_id == role_id)
            )
        )
        .scalars()
        .all()
    )
    if "*" in codes:
        return set(ALL_CODES)
    return set(codes)


async def load_principal(session: AsyncSession, user_id: str) -> Principal | None:
    user = await session.get(User, user_id)
    if user is None or user.deleted_at is not None or user.status != "active":
        return None
    role = await _role_of(session, user.user_id)
    role_code = role.code if role else ""
    permissions = await _permissions_of(session, role.role_id) if role else set()
    return Principal(
        id=user.user_id,
        tenant_id=user.tenant_id,
        tenant_name=await tenant_name(user.tenant_id),
        username=user.username,
        display_name=user.display_name,
        role_code=role_code,
        role_name=role.name if role else "",
        permissions=permissions,
        last_login_at=user.last_login_at,
    )


def principal_to_user(principal: Principal) -> UserOut:
    return UserOut(
        id=principal.id,
        username=principal.username,
        name=principal.display_name,
        role=principal.role_code,
        role_name=principal.role_name,
        permissions=sorted(principal.permissions),
        tenant=principal.tenant_name,
        last_login_at=fmt_dt(principal.last_login_at),  # type: ignore[arg-type]
    )


async def authenticate(session: AsyncSession, username: str, password: str) -> Principal:
    user = (
        await session.execute(
            select(User).where(
                User.username == username,
                User.deleted_at.is_(None),
            )
        )
    ).scalars().first()
    if user is None or not verify_password(password, user.password_hash):
        raise Unauthorized("用户名或密码错误")
    if user.status != "active":
        raise Unauthorized("账号已停用，请联系管理员")
    principal = await load_principal(session, user.user_id)
    if principal is None:
        raise Unauthorized("账号不可用")
    return principal


def issue_token(principal: Principal) -> tuple[str, int]:
    return create_access_token(
        user_id=principal.id,
        tenant_id=principal.tenant_id,
        role=principal.role_code,
    )


async def touch_login(session: AsyncSession, user_id: str) -> None:
    user = await session.get(User, user_id)
    if user is not None:
        user.last_login_at = utcnow()
        await session.commit()


async def list_users(
    session: AsyncSession, *, tenant_id: str | None, page: int, page_size: int
) -> tuple[list[UserOut], int]:
    stmt = select(User).where(User.deleted_at.is_(None))
    count_stmt = select(func.count()).select_from(User).where(User.deleted_at.is_(None))
    if tenant_id:
        stmt = stmt.where(User.tenant_id == tenant_id)
        count_stmt = count_stmt.where(User.tenant_id == tenant_id)
    total = int((await session.execute(count_stmt)).scalar_one())
    users = (
        (
            await session.execute(
                stmt.order_by(User.created_at.asc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    tenant_names: dict[str, str] = {}
    result: list[UserOut] = []
    for user in users:
        if user.tenant_id not in tenant_names:
            tenant_names[user.tenant_id] = await tenant_name(user.tenant_id)
        role = await _role_of(session, user.user_id)
        permissions = await _permissions_of(session, role.role_id) if role else set()
        result.append(
            UserOut(
                id=user.user_id,
                username=user.username,
                name=user.display_name,
                role=role.code if role else "",
                role_name=role.name if role else "",
                permissions=sorted(permissions),
                tenant=tenant_names[user.tenant_id],
                last_login_at=fmt_dt(user.last_login_at),
            )
        )
    return result, total


async def list_roles(session: AsyncSession) -> list[RoleRowOut]:
    roles = (await session.execute(select(Role).order_by(Role.created_at.asc()))).scalars().all()
    result: list[RoleRowOut] = []
    for role in roles:
        user_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(UserRole)
                    .where(UserRole.role_id == role.role_id)
                )
            ).scalar_one()
        )
        permission_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(RolePermission)
                    .where(RolePermission.role_id == role.role_id)
                )
            ).scalar_one()
        )
        if role.code == "super_admin":
            permission_count = len(ALL_CODES)
        result.append(
            RoleRowOut(
                code=role.code,
                name=role.name,
                description=role.description,
                user_count=user_count,
                permission_count=permission_count,
            )
        )
    return result


async def list_tenants(session: AsyncSession, *, page: int, page_size: int) -> tuple[list[TenantOut], int]:
    cursor = col("tenants").find({}).sort("_id", 1).skip((page - 1) * page_size).limit(page_size)
    docs = [doc async for doc in cursor]
    total = await col("tenants").count_documents({})
    counts = dict(
        (
            await session.execute(
                select(User.tenant_id, func.count())
                .where(User.deleted_at.is_(None))
                .group_by(User.tenant_id)
            )
        ).all()
    )
    items = [
        TenantOut(
            id=doc["_id"],
            name=doc.get("name", ""),
            plan=doc.get("plan", "标准版"),
            users=int(counts.get(doc["_id"], 0)),
            status=doc.get("status", "active"),
        )
        for doc in docs
    ]
    return items, int(total)


def dictionaries() -> dict[str, list[str]]:
    return {
        "subject": ["数学", "物理", "化学", "语文", "英语"],
        "stage": ["初中", "高中"],
        "questionType": ["选择题", "填空题", "解答题"],
        "difficulty": ["易", "中", "难"],
        "jobStatus": ["pending", "running", "succeeded", "failed", "canceled"],
        "reviewPriority": ["normal", "high"],
        "tenantPlan": ["旗舰版", "标准版"],
    }


def role_definition(code: str) -> dict | None:
    return ROLE_BY_CODE.get(code)
