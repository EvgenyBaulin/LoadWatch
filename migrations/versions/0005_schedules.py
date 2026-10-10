"""Расписания тестов сайтов.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-10 19:03:05.442989+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Применить ревизию."""
    op.create_table(
        "schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=True),
        sa.Column("cron", sa.String(length=100), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_schedules_scenario_id_scenarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_schedules_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedules")),
        sa.UniqueConstraint("site_id", name=op.f("uq_schedules_site_id")),
    )
    op.create_index(
        "ix_schedules_enabled_next_run_at", "schedules", ["enabled", "next_run_at"], unique=False
    )
    op.create_index(op.f("ix_schedules_scenario_id"), "schedules", ["scenario_id"], unique=False)


def downgrade() -> None:
    """Откатить ревизию."""
    op.drop_index(op.f("ix_schedules_scenario_id"), table_name="schedules")
    op.drop_index("ix_schedules_enabled_next_run_at", table_name="schedules")
    op.drop_table("schedules")
