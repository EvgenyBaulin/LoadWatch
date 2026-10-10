"""Сценарий нагрузочного теста: профиль нагрузки, поток запросов, SLO и Apdex."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from loadwatch.db import Base, TimestampMixin

# В PostgreSQL — JSONB (компактнее и индексируется), в остальных базах — обычный JSON.
# none_as_null: Python None — это SQL NULL, а не JSON-значение null (иначе IS NULL не сработает).
JsonType = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def default_flow() -> list[dict[str, Any]]:
    """Поток по умолчанию: один GET главной страницы."""
    return [{"name": "GET /", "method": "GET", "path": "/"}]


class Scenario(TimestampMixin, Base):
    """Что и как нагружать на сайте."""

    __tablename__ = "scenarios"
    __table_args__ = (UniqueConstraint("site_id", "name", name="uq_scenarios_site_id_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sites.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # {"type": "constant", "rate": 10, "duration_sec": 60} — см. loadwatch.runner.profiles
    load_profile: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    # Список шагов потока; формат шагов — loadwatch.flow (фаза 4).
    flow: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, nullable=False, default=default_flow
    )
    apdex_t_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    slo_p95_ms: Mapped[int | None] = mapped_column(Integer)
    slo_max_error_rate: Mapped[float | None] = mapped_column(Float)

    def __repr__(self) -> str:
        return f"Scenario(id={self.id!s}, name={self.name!r})"
