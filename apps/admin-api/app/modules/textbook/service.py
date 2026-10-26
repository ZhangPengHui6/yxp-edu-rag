"""Textbook use cases and DTO mapping."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AppError, NotFound
from app.modules.audit import service as audit
from app.modules.textbook import repository as repo
from app.modules.textbook.schemas import ChapterOut, LicenseOut, LicenseUpdateIn, TextbookOut
from app.shared.time import fmt_date

LICENSE_WARN_DAYS = 90


def _license_out(doc: dict | None) -> LicenseOut:
    if not doc or not doc.get("end_at"):
        return LicenseOut(holder="未绑定", scope="—", expires_at="—", status="none")
    end_at = doc["end_at"]
    if isinstance(end_at, datetime):
        end_date = end_at.date()
        expires_at = fmt_date(end_at)
    elif isinstance(end_at, date):
        end_date = end_at
        expires_at = end_at.isoformat()
    else:
        try:
            end_date = datetime.strptime(str(end_at)[:10], "%Y-%m-%d").date()
        except ValueError:
            return LicenseOut(holder="未绑定", scope="—", expires_at="—", status="none")
        expires_at = end_date.isoformat()
    today = datetime.now(UTC).date()
    status: Literal["valid", "expiring", "none"]
    if end_date < today:
        status = "none"
    elif end_date <= today + timedelta(days=LICENSE_WARN_DAYS):
        status = "expiring"
    else:
        status = "valid"
    return LicenseOut(
        holder=doc.get("holder", ""),
        scope=doc.get("scope", ""),
        expires_at=expires_at,
        status=status,
    )


def _to_out(book: dict, chapters: list[dict], license_doc: dict | None) -> TextbookOut:
    return TextbookOut(
        id=book["_id"],
        title=book.get("title", ""),
        edition=book.get("edition", ""),
        subject=book.get("subject", ""),
        stage=book.get("stage", "middle"),  # type: ignore[arg-type]
        chapters=[
            ChapterOut(
                id=chapter["_id"],
                no=int(chapter.get("no", 0)),
                title=chapter.get("title", ""),
                page=int(chapter.get("page", 0)),
                question_count=int(chapter.get("question_count", 0)),
            )
            for chapter in chapters
        ],
        page_count=int(book.get("page_count", 0)),
        status=book.get("status", "draft"),  # type: ignore[arg-type]
        license=_license_out(license_doc),
        updated_at=fmt_date(book.get("updated_at")),
    )


async def list_textbooks(
    *,
    tenant_id: str,
    page: int,
    page_size: int,
    status: str | None,
    subject: str | None,
    keyword: str | None,
) -> tuple[list[TextbookOut], int]:
    books, total = await repo.list_books(
        tenant_id,
        page=page,
        page_size=page_size,
        status=status,
        subject=subject,
        keyword=keyword,
    )
    ids = [book["_id"] for book in books]
    chapters = await repo.chapters_for(ids)
    licenses = await repo.licenses_for(ids)
    return [
        _to_out(book, chapters.get(book["_id"], []), licenses.get(book["_id"])) for book in books
    ], total


async def get_textbook(tenant_id: str, book_id: str) -> TextbookOut:
    book = await repo.get_book(tenant_id, book_id)
    if book is None:
        raise NotFound("教材不存在")
    return _to_out(book, await repo.get_chapters(book_id), await repo.get_license(book_id))


async def get_chapters(tenant_id: str, book_id: str) -> list[ChapterOut]:
    book = await repo.get_book(tenant_id, book_id)
    if book is None:
        raise NotFound("教材不存在")
    return [
        ChapterOut(
            id=chapter["_id"],
            no=int(chapter.get("no", 0)),
            title=chapter.get("title", ""),
            page=int(chapter.get("page", 0)),
            question_count=int(chapter.get("question_count", 0)),
        )
        for chapter in await repo.get_chapters(book_id)
    ]


async def update_license(
    session: AsyncSession,
    *,
    user: CurrentUser,
    book_id: str,
    payload: LicenseUpdateIn,
) -> LicenseOut:
    book = await repo.get_book(user.tenant_id, book_id)
    if book is None:
        raise NotFound("教材不存在")
    try:
        expires = datetime.strptime(payload.expires_at, "%Y-%m-%d").date()
    except ValueError as exc:
        raise AppError("授权到期日格式应为 YYYY-MM-DD", details={"value": payload.expires_at}) from exc
    before = _license_out(await repo.get_license(book_id)).model_dump(by_alias=True)
    doc = await repo.upsert_license(
        user.tenant_id,
        book_id,
        holder=payload.holder,
        scope=payload.scope,
        expires_at=expires.isoformat(),
    )
    after = _license_out(doc).model_dump(by_alias=True)
    await col_update_book_license(user.tenant_id, book_id, doc.get("_id", ""))
    audit.record(
        session,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_name=user.name,
        action="textbook.license.update",
        resource_type="textbook",
        resource_id=book_id,
        before=before,
        after=after,
    )
    audit.emit(
        session,
        tenant_id=user.tenant_id,
        event_type="textbook.license.updated",
        aggregate_type="textbook",
        aggregate_id=book_id,
        payload=after,
    )
    await session.commit()
    return _license_out(doc)


async def col_update_book_license(tenant_id: str, book_id: str, license_id: str) -> None:
    from app.infra.mongo import col

    await col("textbooks").update_one(
        {"_id": book_id, "tenant_id": tenant_id},
        {"$set": {"license_id": license_id, "updated_at": datetime.now(UTC)}},
    )
