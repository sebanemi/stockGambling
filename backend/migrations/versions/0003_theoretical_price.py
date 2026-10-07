"""Add theoretical CEDEAR price table.

Revision ID: 0003_theoretical_price
Revises: 0002_market_data
Create Date: 2026-09-30

The theoretical price is an accounting identity:
    theoretical = underlying_price x fx / ratio

Every row stores the three inputs so any historical value can be
re-derived and audited. Premium/discount is computed from the stored
actual CEDEAR close.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_theoretical_price"
down_revision: str | None = "0002_market_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the Phase 4 theoretical price table."""
    op.create_table(
        "sg_theoretical_price_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column("theoretical_price", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("ratio_used", sa.Numeric(precision=20, scale=10), nullable=False),
        sa.Column("fx_used", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("underlying_price_used", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("local_price", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("premium_discount", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("underlying_market_date", sa.Date(), nullable=False),
        sa.Column("fx_market_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["sg_instruments.id"],
            name=op.f("fk_sg_theoretical_price_history_instrument_id_sg_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sg_theoretical_price_history")),
        sa.CheckConstraint("theoretical_price > 0", name=op.f("ck_sg_theoretical_price_history_theoretical_positive")),
        sa.CheckConstraint("ratio_used > 0", name=op.f("ck_sg_theoretical_price_history_ratio_positive")),
        sa.CheckConstraint("fx_used > 0", name=op.f("ck_sg_theoretical_price_history_fx_positive")),
        sa.CheckConstraint("underlying_price_used > 0", name=op.f("ck_sg_theoretical_price_history_underlying_positive")),
        sa.UniqueConstraint(
            "instrument_id",
            "market_date",
            name="uq_sg_theoretical_price_history_instrument_id",
        ),
    )
    op.create_index(
        "ix_sg_theoretical_price_history_market_date",
        "sg_theoretical_price_history",
        ["market_date"],
    )
    op.create_index(
        "ix_sg_theoretical_price_history_underlying_date",
        "sg_theoretical_price_history",
        ["underlying_market_date"],
    )


def downgrade() -> None:
    """Drop the Phase 4 theoretical price table."""
    op.drop_index(
        "ix_sg_theoretical_price_history_underlying_date",
        table_name="sg_theoretical_price_history",
    )
    op.drop_index(
        "ix_sg_theoretical_price_history_market_date",
        table_name="sg_theoretical_price_history",
    )
    op.drop_table("sg_theoretical_price_history")