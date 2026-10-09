"""Backtest persistence: simulate over stored bars and store the run.

The API (``POST /api/v1/backtests``) and the demo seeder share this path so
a persisted run is always the engine's verbatim output over stored closes -
never recomputed at read time, never hand-assembled.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
from sqlalchemy.orm import Session

from app.backtesting.costs import BacktestCosts
from app.backtesting.portfolio import run_backtest
from app.models.backtest import Backtest


def save_backtest(
    session: Session,
    *,
    instrument_id: int,
    start: date,
    end: date,
    closes: list[float],
    signals: list[int],
    holding_period: int = 1,
    initial_capital: float = 1_000_000.0,
    costs: BacktestCosts | None = None,
    position_fraction: float = 1.0,
    notes: str | None = None,
) -> Backtest:
    """Simulate ``signals`` over ``closes`` and persist the run.

    Args:
        session: Database session (the caller owns the commit policy; this
            function commits the new row before returning).
        instrument_id: The CEDEAR the bars belong to.
        start: Start market date (inclusive).
        end: End market date (inclusive).
        closes: Stored closes, oldest first.
        signals: Binary targets, one per holding-period epoch.
        holding_period: Sessions each signal is held.
        initial_capital: Starting cash in ARS.
        costs: Commission + slippage model (non-zero by default).
        position_fraction: Cash fraction deployed on each entry.
        notes: Optional audit note (e.g. which model produced the signals).

    Returns:
        The persisted row (refreshed, with its id).
    """
    model = costs or BacktestCosts()
    result = run_backtest(
        np.asarray(closes, dtype=np.float64),
        np.asarray(signals, dtype=np.int64),
        initial_capital=initial_capital,
        costs=model,
        position_fraction=position_fraction,
        holding_period=holding_period,
    )
    row = Backtest(
        instrument_id=instrument_id,
        start_date=start,
        end_date=end,
        n_bars=len(closes),
        params={
            "initial_capital": initial_capital,
            "position_fraction": position_fraction,
            "commission_rate": model.commission_rate,
            "slippage_rate": model.slippage_rate,
            "holding_period": holding_period,
        },
        signals=[int(v) for v in signals],
        trades=[
            {
                "entry_index": trade.entry_index,
                "exit_index": trade.exit_index,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "shares": trade.shares,
                "costs": trade.costs,
                "pnl": trade.pnl,
            }
            for trade in result.trades
        ],
        equity_curve=[float(v) for v in result.equity_curve],
        benchmark_buy_hold=[float(v) for v in result.benchmark_buy_hold],
        metrics=dict(result.metrics),
        benchmark_metrics=dict(result.benchmark_metrics),
        total_costs=Decimal(str(result.total_costs)),
        status="succeeded",
        notes=notes,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


__all__ = ["save_backtest"]
