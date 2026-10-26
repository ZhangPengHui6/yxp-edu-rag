"""Page-based pagination (the admin UI contract)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

T = TypeVar("T")


@dataclass(slots=True)
class PageParams:
    page: int = 1
    page_size: int = 10

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_params(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, alias="pageSize", ge=1, le=100),
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


class PageResult(BaseModel, Generic[T]):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )

    items: list[T]
    total: int
    page: int
    page_size: int = Field(serialization_alias="pageSize", validation_alias="pageSize")


def page_result(items: list[T], total: int, params: PageParams) -> PageResult[T]:
    return PageResult[T](
        items=items, total=total, page=params.page, page_size=params.page_size
    )
