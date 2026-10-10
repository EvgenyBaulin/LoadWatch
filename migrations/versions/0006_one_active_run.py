"""Не больше одного активного запуска на сайт (частичный уникальный индекс).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-10 19:11:14.049118+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Применить ревизию."""
    op.create_index(
        "uq_test_runs_one_active_per_site",
        "test_runs",
        ["site_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    """Откатить ревизию."""
    op.drop_index(
        "uq_test_runs_one_active_per_site",
        table_name="test_runs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
