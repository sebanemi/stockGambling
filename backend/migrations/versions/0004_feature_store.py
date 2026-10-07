"""Add feature snapshot table.

Revision ID: 0004_feature_store
Revises: 0003_theoretical_price
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_feature_store"
down_revision: str | None = "0003_theoretical_price"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "sg_feature_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("feature_version", sa.String(length=32), nullable=False),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column(
            "feature_values_json", sa.String(), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["sg_instruments.id"],
            name=op.f("fk_sg_feature_snapshots_instrument_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sg_feature_snapshots")),
        sa.UniqueConstraint(
            "instrument_id", "as_of", "feature_version",
            name=op.f("uq_sg_feature_snapshots_instrument_as_of_version"),
        ),
    )
    op.create_index(
        op.f("ix_sg_feature_snapshots_market_date"),
        "sg_feature_snapshots", ["market_date"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_sg_feature_snapshots_market_date"),
        table_name="sg_feature_snapshots",
    )
    op.drop_table("sg_feature_snapshots")
