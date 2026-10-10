"""Общие схемы API: пагинация."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PageParams(BaseModel):
    """Параметры страницы списка."""

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class Page[T](BaseModel):
    """Страница списка: элементы и общее количество."""

    items: list[T]
    total: int
    limit: int
    offset: int
