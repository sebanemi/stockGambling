"""Add feature snapshot history indexes.

Revision ID: 0005_feature_store_indexes
Revises: 0004_feature_store
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_feature_store_indexes"
down_revision: str | None = "0004_feature_store"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_sg_feature_snapshots_version_market_date"),
        "sg_feature_snapshots",
        ["feature_version", "market_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sg_feature_snapshots_instrument_market_date"),
        "sg_feature_snapshots",
        ["instrument_id", "market_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_sg_feature_snapshots_instrument_market_date"),
        table_name="sg_feature_snapshots",
    )
    op.drop_index(
        op.f("ix_sg_feature_snapshots_version_market_date"),
        table_name="sg_feature_snapshots",
    )
