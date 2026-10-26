from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require
from app.core.envelope import Envelope, ok
from app.core.pagination import PageParams, PageResult, page_params, page_result
from app.infra.pg import get_session
from app.modules.question import service
from app.modules.question.schemas import (
    DuplicatePairOut,
    QuestionOut,
    QuestionUpdateIn,
    VariantOut,
)

router = APIRouter(prefix="/questions", tags=["questions"])


@router.get("", response_model=Envelope[PageResult[QuestionOut]])
async def list_questions(
    status: str | None = Query(default=None),
    type_: str | None = Query(default=None, alias="type"),
    difficulty: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(require("question.view")),
):
    items, total = await service.list_questions(
        tenant_id=user.tenant_id,
        page=params.page,
        page_size=params.page_size,
        status=status,
        type_label_value=type_,
        difficulty_label_value=difficulty,
        keyword=keyword,
    )
    return ok(page_result(items, total, params))


@router.get("/duplicates", response_model=Envelope[list[DuplicatePairOut]])
async def list_duplicates(
    user: CurrentUser = Depends(require("question.dedupe")),
):
    return ok(await service.get_duplicates(user.tenant_id))


@router.get("/{question_id}", response_model=Envelope[QuestionOut])
async def get_question(
    question_id: str,
    user: CurrentUser = Depends(require("question.view")),
):
    return ok(await service.get_question(user.tenant_id, question_id))


@router.get("/{question_id}/variants", response_model=Envelope[list[VariantOut]])
async def get_variants(
    question_id: str,
    user: CurrentUser = Depends(require("question.variant")),
):
    return ok(await service.get_variants(user.tenant_id, question_id))


@router.patch("/{question_id}", response_model=Envelope[QuestionOut])
async def update_question(
    question_id: str,
    payload: QuestionUpdateIn,
    user: CurrentUser = Depends(require("question.edit")),
    session: AsyncSession = Depends(get_session),
):
    return ok(
        await service.update_question(
            session, user=user, question_id=question_id, payload=payload
        )
    )
