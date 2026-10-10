"""Алерты, каналы уведомлений, метки на графиках, настройки сводки, токен триггера.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-10 19:36:33.711737+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Применить ревизию."""
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "config",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("secret_config", sa.Text(), nullable=True),
        sa.Column(
            "events",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('telegram', 'email', 'slack', 'discord', 'webhook')",
            name=op.f("ck_notification_channels_type"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_channels_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_channels")),
    )
    op.create_index(
        op.f("ix_notification_channels_user_id"), "notification_channels", ["user_id"], unique=False
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "details",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('open', 'resolved')", name=op.f("ck_alerts_status")),
        sa.CheckConstraint(
            "type IN ('regression', 'slo_breach', 'run_failed', 'site_unverified')",
            name=op.f("ck_alerts_type"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["test_runs.id"],
            name=op.f("fk_alerts_run_id_test_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_alerts_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
    )
    op.create_index("ix_alerts_site_id_opened_at", "alerts", ["site_id", "opened_at"], unique=False)
    op.create_index(
        "uq_alerts_one_open_per_site_type",
        "alerts",
        ["site_id", "type"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "annotations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('deploy', 'comment')", name=op.f("ck_annotations_kind")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["test_runs.id"],
            name=op.f("fk_annotations_run_id_test_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_annotations_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_annotations")),
    )
    op.create_index("ix_annotations_site_id_at", "annotations", ["site_id", "at"], unique=False)
    op.add_column("sites", sa.Column("trigger_token_hash", sa.String(length=64), nullable=True))
    # server_default — чтобы существующие пользователи получили значения по умолчанию.
    op.add_column(
        "users", sa.Column("digest_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "users", sa.Column("digest_weekday", sa.SmallInteger(), nullable=False, server_default="0")
    )
    op.add_column(
        "users", sa.Column("digest_hour", sa.SmallInteger(), nullable=False, server_default="9")
    )
    op.create_check_constraint("digest_weekday", "users", "digest_weekday BETWEEN 0 AND 6")
    op.create_check_constraint("digest_hour", "users", "digest_hour BETWEEN 0 AND 23")
    op.add_column("users", sa.Column("last_digest_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Откатить ревизию."""
    op.drop_constraint(op.f("ck_users_digest_hour"), "users", type_="check")
    op.drop_constraint(op.f("ck_users_digest_weekday"), "users", type_="check")
    op.drop_column("users", "last_digest_at")
    op.drop_column("users", "digest_hour")
    op.drop_column("users", "digest_weekday")
    op.drop_column("users", "digest_enabled")
    op.drop_column("sites", "trigger_token_hash")
    op.drop_index("ix_annotations_site_id_at", table_name="annotations")
    op.drop_table("annotations")
    op.drop_index(
        "uq_alerts_one_open_per_site_type",
        table_name="alerts",
        postgresql_where=sa.text("status = 'open'"),
    )
    op.drop_index("ix_alerts_site_id_opened_at", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index(op.f("ix_notification_channels_user_id"), table_name="notification_channels")
    op.drop_table("notification_channels")
