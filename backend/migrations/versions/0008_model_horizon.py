"""Model registry gains a horizon dimension.

Revision ID: 0008_model_horizon
Revises: 0007_backtests
Create Date: 2026-10-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_model_horizon"
down_revision: str | None = "0007_backtests"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column(
        "sg_models",
        sa.Column("horizon", sa.String(length=8), server_default="1d", nullable=False),
    )
    op.drop_constraint(op.f("uq_sg_models_name"), "sg_models", type_="unique")
    op.create_unique_constraint(
        op.f("uq_sg_models_name_horizon"), "sg_models", ["name", "horizon"]
    )
    op.create_index(op.f("ix_sg_models_horizon"), "sg_models", ["horizon"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sg_models_horizon"), table_name="sg_models")
    op.drop_constraint(op.f("uq_sg_models_name_horizon"), "sg_models", type_="unique")
    op.create_unique_constraint(op.f("uq_sg_models_name"), "sg_models", ["name"])
    op.drop_column("sg_models", "horizon")
