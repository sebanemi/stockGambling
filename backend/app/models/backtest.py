"""Backtest-run database model.

``sg_backtests`` records one CEDEAR portfolio simulation over stored local
price bars: the instrument, the market-date window, the exact input signals,
the cost configuration, the closed round-trip trades and the resulting
equity curves and metrics. Everything the ``GET`` endpoint returns comes
from this row - nothing is recomputed at read time, so a published result
can always be re-derived and audited.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.instrument import Instrument


class Backtest(Base, TimestampMixin):
    """One persisted backtest run over stored CEDEAR bars."""

    __tablename__ = "sg_backtests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("sg_instruments.id", ondelete="CASCADE"), nullable=False
    )

    #: Inclusive market-date window the simulation ran over.
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Stored bars in the window (signals must be ``n_bars - 1``).
    n_bars: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Input configuration: initial capital, position fraction and both rates.
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: Binary position targets, one per interval, oldest first.
    signals: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    #: Closed round-trip trades as plain dicts (entry/exit, prices, pnl).
    trades: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    #: Strategy equity per bar in ARS, oldest first.
    equity_curve: Mapped[list[float]] = mapped_column(JSONB, nullable=False, default=list)
    #: Buy-and-hold CEDEAR equity per bar in ARS, oldest first.
    benchmark_buy_hold: Mapped[list[float]] = mapped_column(JSONB, nullable=False, default=list)

    #: Strategy performance (same keys as the engine's metric dict).
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: Buy-and-hold performance on the same window.
    benchmark_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: Commissions paid on every fill, in ARS.
    total_costs: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="succeeded")
    notes: Mapped[str | None] = mapped_column(String(512))

    instrument: Mapped[Instrument] = relationship()

    __table_args__ = (Index("ix_sg_backtests_instrument_id", "instrument_id"),)


__all__ = ["Backtest"]
