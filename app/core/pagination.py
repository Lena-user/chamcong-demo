from dataclasses import dataclass
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

from app.core.config import get_settings

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class PageParams:
    page: int
    size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


def page_params(page: int = Query(1, ge=1), size: int | None = Query(None, ge=1)) -> PageParams:
    """`size` defaults to DEFAULT_PAGE_SIZE and is clamped to MAX_PAGE_SIZE."""
    s = get_settings()
    return PageParams(page=page, size=min(size or s.default_page_size, s.max_page_size))
