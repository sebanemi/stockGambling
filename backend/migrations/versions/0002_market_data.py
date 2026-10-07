"""Add price-history tables: local CEDEAR bars, underlying bars, and FX.

Revision ID: 0002_market_data
Revises: 0001_cedear_metadata
Create Date: 2026-09-29

Written by hand so the revision semantics are reviewable: a re-ingest of the
same (instrument, market_date) replaces the row in place (prices get revised),
there is no exclusion constraint here like the ratio table has, and the FX
table is intentionally global - a pair is not a per-instrument series.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_market_data"
down_revision: str | None = "0001_cedear_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _price_columns(
    *,
    symbol_length: int = 64,
) -> list[sa.Column]:
    """The OHLCV column block shared by the two instrument-scoped tables."""
    return [
        sa.Column("symbol", sa.String(length=symbol_length), nullable=False),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("high", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("low", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("close", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("adjusted_close", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("traded_value", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("trades", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    """Create the Phase 3 price-history tables."""
    op.create_table(
        "sg_local_price_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        *_price_columns(),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["sg_instruments.id"],
            name=op.f("fk_sg_local_price_history_instrument_id_sg_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sg_local_price_history")),
        sa.UniqueConstraint(
            "instrument_id",
            "market_date",
            name="uq_sg_local_price_history_instrument_id",
        ),
    )
    op.create_index(
        "ix_sg_local_price_history_market_date", "sg_local_price_history", ["market_date"]
    )

    op.create_table(
        "sg_underlying_price_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        *_price_columns(),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["sg_instruments.id"],
            name=op.f("fk_sg_underlying_price_history_instrument_id_sg_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sg_underlying_price_history")),
        sa.UniqueConstraint(
            "instrument_id",
            "market_date",
            name="uq_sg_underlying_price_history_instrument_id",
        ),
    )
    op.create_index(
        "ix_sg_underlying_price_history_market_date",
        "sg_underlying_price_history",
        ["market_date"],
    )

    op.create_table(
        "sg_fx_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pair", sa.String(length=16), nullable=False),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("high", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("low", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("close", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="ARS"),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=256), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sg_fx_history")),
        sa.UniqueConstraint("pair", "source", "market_date", name="uq_sg_fx_history_pair_source"),
    )
    op.create_index("ix_sg_fx_history_pair_date", "sg_fx_history", ["pair", "market_date"])


def downgrade() -> None:
    """Drop the Phase 3 price-history tables."""
    op.drop_table("sg_fx_history")
    op.drop_index(
        "ix_sg_underlying_price_history_market_date", table_name="sg_underlying_price_history"
    )
    op.drop_table("sg_underlying_price_history")
    op.drop_index("ix_sg_local_price_history_market_date", table_name="sg_local_price_history")
    op.drop_table("sg_local_price_history")
