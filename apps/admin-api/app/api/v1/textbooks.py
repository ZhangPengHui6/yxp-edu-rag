from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require
from app.core.envelope import Envelope, ok
from app.core.pagination import PageParams, PageResult, page_params, page_result
from app.infra.pg import get_session
from app.modules.textbook import service
from app.modules.textbook.schemas import ChapterOut, LicenseOut, LicenseUpdateIn, TextbookOut

router = APIRouter(prefix="/textbooks", tags=["textbooks"])


@router.get("", response_model=Envelope[PageResult[TextbookOut]])
async def list_textbooks(
    status: str | None = Query(default=None),
    subject: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(require("textbook.view")),
):
    items, total = await service.list_textbooks(
        tenant_id=user.tenant_id,
        page=params.page,
        page_size=params.page_size,
        status=status,
        subject=subject,
        keyword=keyword,
    )
    return ok(page_result(items, total, params))


@router.get("/{book_id}", response_model=Envelope[TextbookOut])
async def get_textbook(
    book_id: str,
    user: CurrentUser = Depends(require("textbook.view")),
):
    return ok(await service.get_textbook(user.tenant_id, book_id))


@router.get("/{book_id}/chapters", response_model=Envelope[list[ChapterOut]])
async def list_chapters(
    book_id: str,
    user: CurrentUser = Depends(require("textbook.view")),
):
    return ok(await service.get_chapters(user.tenant_id, book_id))


@router.patch("/{book_id}/license", response_model=Envelope[LicenseOut])
async def update_license(
    book_id: str,
    payload: LicenseUpdateIn,
    user: CurrentUser = Depends(require("textbook.license")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.update_license(session, user=user, book_id=book_id, payload=payload))
