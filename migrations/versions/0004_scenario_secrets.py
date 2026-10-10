"""Зашифрованные секреты сценариев.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-10 18:44:38.636823+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Применить ревизию."""
    op.create_table(
        "scenario_secrets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("value_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_scenario_secrets_scenario_id_scenarios"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenario_secrets")),
        sa.UniqueConstraint("scenario_id", "name", name="uq_scenario_secrets_scenario_id_name"),
    )
    op.create_index(
        op.f("ix_scenario_secrets_scenario_id"), "scenario_secrets", ["scenario_id"], unique=False
    )


def downgrade() -> None:
    """Откатить ревизию."""
    op.drop_index(op.f("ix_scenario_secrets_scenario_id"), table_name="scenario_secrets")
    op.drop_table("scenario_secrets")
