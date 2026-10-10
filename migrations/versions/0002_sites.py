"""Таблица сайтов и проверки владения.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10 16:46:02.085757+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Применить ревизию."""
    op.create_table(
        "sites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=300), nullable=False),
        sa.Column("verification_status", sa.String(length=16), nullable=False),
        sa.Column("verification_method", sa.String(length=8), nullable=True),
        sa.Column("verification_token", sa.String(length=64), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "verification_method IS NULL OR verification_method IN ('dns', 'http')",
            name=op.f("ck_sites_verification_method"),
        ),
        sa.CheckConstraint(
            "verification_status IN ('pending', 'verified', 'unverified')",
            name=op.f("ck_sites_verification_status"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_sites_owner_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sites")),
        sa.UniqueConstraint("owner_id", "url", name="uq_sites_owner_id_url"),
    )
    op.create_index(op.f("ix_sites_owner_id"), "sites", ["owner_id"], unique=False)
    op.create_index(
        "ix_sites_status_last_verified_at",
        "sites",
        ["verification_status", "last_verified_at"],
        unique=False,
    )


def downgrade() -> None:
    """Откатить ревизию."""
    op.drop_index("ix_sites_status_last_verified_at", table_name="sites")
    op.drop_index(op.f("ix_sites_owner_id"), table_name="sites")
    op.drop_table("sites")
