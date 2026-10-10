"""Итог поиска регрессии у запуска.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-10 19:17:41.663851+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Применить ревизию."""
    op.add_column(
        "test_runs",
        sa.Column(
            "regression",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Откатить ревизию."""
    op.drop_column("test_runs", "regression")
