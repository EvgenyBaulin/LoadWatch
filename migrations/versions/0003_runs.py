"""Сценарии, запуски и метрики запусков.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-10 17:57:07.797990+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Применить ревизию."""
    op.create_table(
        "scenarios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "load_profile",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "flow",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("apdex_t_ms", sa.Integer(), nullable=False),
        sa.Column("slo_p95_ms", sa.Integer(), nullable=True),
        sa.Column("slo_max_error_rate", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_scenarios_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenarios")),
        sa.UniqueConstraint("site_id", "name", name="uq_scenarios_site_id_name"),
    )
    op.create_index(op.f("ix_scenarios_site_id"), "scenarios", ["site_id"], unique=False)
    op.create_table(
        "test_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column(
            "params",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("params_hash", sa.String(length=64), nullable=True),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("generator_saturated", sa.Boolean(), nullable=False),
        sa.Column("slo_met", sa.Boolean(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('manual', 'schedule', 'hook')", name=op.f("ck_test_runs_source")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'finished', 'failed', 'cancelled')",
            name=op.f("ck_test_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_test_runs_scenario_id_scenarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_test_runs_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_runs")),
    )
    op.create_index(op.f("ix_test_runs_params_hash"), "test_runs", ["params_hash"], unique=False)
    op.create_index(op.f("ix_test_runs_scenario_id"), "test_runs", ["scenario_id"], unique=False)
    op.create_index(
        "ix_test_runs_site_id_started_at", "test_runs", ["site_id", "started_at"], unique=False
    )
    op.create_index(
        "ix_test_runs_status_queued_at", "test_runs", ["status", "queued_at"], unique=False
    )
    op.create_table(
        "run_metrics",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("duration_sec", sa.Float(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("error_rate", sa.Float(), nullable=False),
        sa.Column("rps", sa.Float(), nullable=False),
        sa.Column("iterations_per_sec", sa.Float(), nullable=False),
        sa.Column("latency_avg_ms", sa.Float(), nullable=False),
        sa.Column("latency_p50_ms", sa.Float(), nullable=False),
        sa.Column("latency_p90_ms", sa.Float(), nullable=False),
        sa.Column("latency_p95_ms", sa.Float(), nullable=False),
        sa.Column("latency_p99_ms", sa.Float(), nullable=False),
        sa.Column("latency_max_ms", sa.Float(), nullable=False),
        sa.Column("apdex", sa.Float(), nullable=True),
        sa.Column("ceiling_rps", sa.Float(), nullable=True),
        sa.Column(
            "summary",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "series",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["test_runs.id"],
            name=op.f("fk_run_metrics_run_id_test_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_run_metrics")),
    )


def downgrade() -> None:
    """Откатить ревизию."""
    op.drop_table("run_metrics")
    op.drop_index("ix_test_runs_status_queued_at", table_name="test_runs")
    op.drop_index("ix_test_runs_site_id_started_at", table_name="test_runs")
    op.drop_index(op.f("ix_test_runs_scenario_id"), table_name="test_runs")
    op.drop_index(op.f("ix_test_runs_params_hash"), table_name="test_runs")
    op.drop_table("test_runs")
    op.drop_index(op.f("ix_scenarios_site_id"), table_name="scenarios")
    op.drop_table("scenarios")
