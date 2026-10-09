"""Backtest-run persistence.

Revision ID: 0007_backtests
Revises: 0006_model_registry
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007_backtests"
down_revision: str | None = "0006_model_registry"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "sg_backtests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("n_bars", sa.Integer(), nullable=False),
        sa.Column("params", JSONB(), nullable=False),
        sa.Column("signals", JSONB(), nullable=False),
        sa.Column("trades", JSONB(), nullable=False),
        sa.Column("equity_curve", JSONB(), nullable=False),
        sa.Column("benchmark_buy_hold", JSONB(), nullable=False),
        sa.Column("metrics", JSONB(), nullable=False),
        sa.Column("benchmark_metrics", JSONB(), nullable=False),
        sa.Column("total_costs", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["sg_instruments.id"],
            name=op.f("fk_sg_backtests_instrument_id_sg_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sg_backtests")),
    )
    op.create_index(
        op.f("ix_sg_backtests_instrument_id"), "sg_backtests", ["instrument_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sg_backtests_instrument_id"), table_name="sg_backtests")
    op.drop_table("sg_backtests")
