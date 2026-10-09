"""CEDEAR backtesting: long/flat portfolio simulation in ARS.

The engine trades the **CEDEAR quoted in ARS on BYMA** - never the
underlying. A signal decided at the close of session ``i`` holds the
position over the interval ``[close[i], close[i + 1]]``:

* ``signal == 1``: be invested (long) over the next session.
* ``signal == 0``: be flat (cash) over the next session.

No shorting: a retail CEDEAR account goes long or stays in cash. Costs
are proportional and non-zero by default, so a strategy whose gross edge
is smaller than round-trip costs reports a loss instead of a profit.
"""

from __future__ import annotations

from app.backtesting.benchmarks import buy_and_hold_curve, cash_curve
from app.backtesting.costs import BacktestCosts
from app.backtesting.metrics import backtest_metrics, to_usd_curve
from app.backtesting.portfolio import (
    BacktestResult,
    Trade,
    run_backtest,
    signals_from_probas,
)
from app.backtesting.store import save_backtest

__all__ = [
    "BacktestCosts",
    "BacktestResult",
    "Trade",
    "backtest_metrics",
    "buy_and_hold_curve",
    "cash_curve",
    "run_backtest",
    "save_backtest",
    "signals_from_probas",
    "to_usd_curve",
]
