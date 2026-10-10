"""Зашифрованные секреты сценария (логины, пароли, токены тестового аккаунта)."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from loadwatch.db import Base, TimestampMixin


class ScenarioSecret(TimestampMixin, Base):
    """Один секрет; значение хранится только зашифрованным."""

    __tablename__ = "scenario_secrets"
    __table_args__ = (
        UniqueConstraint("scenario_id", "name", name="uq_scenario_secrets_scenario_id_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:  # значение в repr не попадает никогда
        return f"ScenarioSecret(name={self.name!r})"
