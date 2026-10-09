"""Long/flat CEDEAR portfolio simulation with honest costs.

Execution model: the signal decided at the close of an epoch boundary bar
is held over the next ``holding_period`` bars (``1`` = every bar decides).
When the target position differs from the current one the engine trades
the full delta at that boundary close, paying slippage (adverse execution
price) plus commission (fraction of the traded notional). Both legs are
recorded so every peso of friction lands in ``total_costs``.

Position sizing is a fraction of the currently available cash deployed
on each new entry (``1.0`` = all-in). Because entries and exits are
all-or-nothing, an always-``1`` signal buys once and holds (exactly the
buy-and-hold benchmark) and an always-``0`` signal never trades (exactly
the cash benchmark).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.backtesting.benchmarks import buy_and_hold_curve, cash_curve
from app.backtesting.costs import BacktestCosts
from app.backtesting.metrics import backtest_metrics, to_usd_curve


@dataclass(frozen=True, slots=True)
class Trade:
    """One closed buy-to-sell round trip (long only)."""

    entry_index: int
    exit_index: int
    entry_price: float
    exit_price: float
    shares: float
    costs: float
    pnl: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """The full outcome of one backtest run (ARS-native)."""

    equity_curve: NDArray[np.float64]
    cash_curve: NDArray[np.float64]
    positions: NDArray[np.int64]
    trades: list[Trade] = field(default_factory=list)
    total_costs: float = 0.0
    metrics: dict[str, float | None] = field(default_factory=dict)
    benchmark_buy_hold: NDArray[np.float64] = field(
        default_factory=lambda: np.zeros(0, dtype=np.float64)
    )
    benchmark_cash: NDArray[np.float64] = field(
        default_factory=lambda: np.zeros(0, dtype=np.float64)
    )
    benchmark_metrics: dict[str, float | None] = field(default_factory=dict)
    metrics_usd: dict[str, float | None] | None = None
    benchmark_metrics_usd: dict[str, float | None] | None = None
    initial_capital: float = 0.0


def signals_from_probas(
    proba_up: NDArray[np.float64] | Any, *, threshold: float = 0.5
) -> NDArray[np.int64]:
    """Convert ``P(up)`` into long/flat signals at a fixed threshold.

    Args:
        proba_up: Model's ``P(up)`` per epoch (one per holding period).
        threshold: Go long when ``P(up) >= threshold``.

    Returns:
        Binary signals (``1`` = long over the next epoch).
    """
    proba = np.asarray(proba_up, dtype=np.float64).reshape(-1)
    if proba.size == 0:
        raise ValueError("Cannot build signals from an empty probability vector")
    if bool(np.any(~np.isfinite(proba)) or np.any(proba < 0.0) or np.any(proba > 1.0)):
        raise ValueError("Probabilities must be finite and in [0, 1]")
    if not 0.0 < threshold < 1.0:
        raise ValueError(f"threshold must be in (0, 1), got {threshold}")
    return (proba >= threshold).astype(np.int64)


def run_backtest(
    prices: NDArray[np.float64] | Any,
    signals: NDArray[np.int64] | Any,
    *,
    initial_capital: float = 1_000_000.0,
    costs: BacktestCosts | None = None,
    position_fraction: float = 1.0,
    holding_period: int = 1,
    fx_ars_per_usd: NDArray[np.float64] | Any | None = None,
) -> BacktestResult:
    """Simulate a long/flat CEDEAR strategy epoch by epoch.

    Args:
        prices: CEDEAR closes in ARS, oldest first (``N`` bars).
        signals: Binary position targets, one per epoch. Epoch ``e`` is
            decided at the close of bar ``e * holding_period`` and held over
            the next ``holding_period`` bars. ``holding_period=1`` is the
            session-by-session simulation (one signal per interval).
        initial_capital: Starting cash in ARS.
        costs: Commission + slippage model (non-zero by default).
        position_fraction: Fraction of available cash deployed on each new
            entry, in ``(0, 1]``. ``1.0`` invests everything.
        holding_period: Sessions each epoch signal is held (``1`` = every
            bar is its own decision). Must divide the bar intervals exactly.
        fx_ars_per_usd: Optional ARS-per-USD series (``N`` bars) for the
            separate USD-adjusted reporting.

    Returns:
        The equity curves, closed trades, costs, ARS metrics, benchmarks
        and - when FX is given - the USD-adjusted metrics.
    """
    series = np.asarray(prices, dtype=np.float64).reshape(-1)
    targets = np.asarray(signals).astype(np.int64).reshape(-1)
    if series.size < 2:
        raise ValueError(f"Need at least 2 prices, got {series.size}")
    if bool(np.any(~np.isfinite(series)) or np.any(series <= 0.0)):
        raise ValueError("Prices must be finite and strictly positive")
    if holding_period < 1 or not float(holding_period).is_integer():
        raise ValueError(f"holding_period must be a positive integer, got {holding_period}")
    epochs = int(holding_period)
    n_intervals = series.size - 1
    if n_intervals % epochs != 0 or targets.shape != (n_intervals // epochs,):
        raise ValueError(
            f"Need one signal per {epochs}-session epoch: got {targets.shape} "
            f"signals for {series.size} prices"
        )
    if bool(np.any((targets != 0) & (targets != 1))):
        raise ValueError("Signals must be binary (0/1)")
    if initial_capital <= 0.0 or not np.isfinite(initial_capital):
        raise ValueError(f"initial_capital must be finite and positive, got {initial_capital}")
    if not 0.0 < position_fraction <= 1.0 or not np.isfinite(position_fraction):
        raise ValueError(f"position_fraction must be in (0, 1], got {position_fraction}")
    model = costs or BacktestCosts()

    fx: NDArray[np.float64] | None = None
    if fx_ars_per_usd is not None:
        fx = np.asarray(fx_ars_per_usd, dtype=np.float64).reshape(-1)
        if fx.shape != series.shape:
            raise ValueError(f"FX must align with prices: {fx.shape} vs {series.shape}")
        if bool(np.any(~np.isfinite(fx)) or np.any(fx <= 0.0)):
            raise ValueError("FX observations must be finite and strictly positive")

    n = series.size
    equity = np.zeros(n, dtype=np.float64)
    cash_track = np.zeros(n, dtype=np.float64)
    positions = np.zeros(n - 1, dtype=np.int64)

    cash = float(initial_capital)
    shares = 0.0
    entry_index = -1
    entry_price = 0.0
    entry_stake = 0.0
    entry_fee = 0.0
    total_costs = 0.0
    trades: list[Trade] = []

    equity[0] = cash
    cash_track[0] = cash

    for bar in range(n - 1):
        epoch = bar // epochs
        target = int(targets[epoch])
        holding = shares > 0.0
        want_holding = target == 1
        # Epoch boundaries are the only decision points: mid-epoch bars
        # only mark the position to market, they never trade.
        decides = bar % epochs == 0
        close = float(series[bar])

        if decides and want_holding and not holding:
            gross = cash * position_fraction
            if gross > 0.0:
                exec_price = model.buy_execution_price(close)
                fee = model.commission_on(gross)
                stake = gross - fee
                if stake > 0.0:
                    shares = stake / exec_price
                    cash -= gross
                    total_costs += fee
                    entry_index = bar
                    entry_price = exec_price
                    entry_stake = stake
                    entry_fee = fee
        elif decides and not want_holding and holding:
            exec_price = model.sell_execution_price(close)
            proceeds = shares * exec_price
            fee = model.commission_on(proceeds)
            cash += proceeds - fee
            total_costs += fee
            trades.append(
                Trade(
                    entry_index=entry_index,
                    exit_index=bar,
                    entry_price=entry_price,
                    exit_price=exec_price,
                    shares=shares,
                    costs=entry_fee + fee,
                    pnl=(proceeds - fee) - entry_stake,
                )
            )
            shares = 0.0
            entry_index = -1

        positions[bar] = 1 if shares > 0.0 else 0
        equity[bar + 1] = cash + shares * float(series[bar + 1])
        cash_track[bar + 1] = cash

    pnls = np.array([t.pnl for t in trades], dtype=np.float64)
    metrics = backtest_metrics(equity, trade_pnls=pnls, total_costs=total_costs)

    buy_hold = buy_and_hold_curve(series, initial_capital=initial_capital, costs=model)
    cash_bench = cash_curve(initial_capital, n)
    benchmark_metrics = backtest_metrics(
        buy_hold,
        trade_pnls=np.zeros(0, dtype=np.float64),
        total_costs=0.0,
    )

    metrics_usd: dict[str, float | None] | None = None
    benchmark_metrics_usd: dict[str, float | None] | None = None
    if fx is not None:
        # Per-trade USD P&L converts each closed round trip at its exit FX;
        # the still-open tail (if any) stays marked in the equity curve only,
        # so no currency is ever mixed inside one metric dict.
        pnls_usd = np.array([t.pnl / float(fx[t.exit_index]) for t in trades], dtype=np.float64)
        open_fee_usd = (entry_fee / float(fx[-1])) if shares > 0.0 else 0.0
        costs_usd = float(
            np.sum([t.costs / float(fx[t.exit_index]) for t in trades]) + open_fee_usd
        )
        metrics_usd = backtest_metrics(
            to_usd_curve(equity, fx), trade_pnls=pnls_usd, total_costs=costs_usd
        )
        benchmark_metrics_usd = backtest_metrics(
            to_usd_curve(buy_hold, fx),
            trade_pnls=np.zeros(0, dtype=np.float64),
            total_costs=0.0,
        )

    return BacktestResult(
        equity_curve=equity,
        cash_curve=cash_track,
        positions=positions,
        trades=trades,
        total_costs=float(total_costs),
        metrics=metrics,
        benchmark_buy_hold=buy_hold,
        benchmark_cash=cash_bench,
        benchmark_metrics=benchmark_metrics,
        metrics_usd=metrics_usd,
        benchmark_metrics_usd=benchmark_metrics_usd,
        initial_capital=float(initial_capital),
    )


__all__ = ["BacktestResult", "Trade", "run_backtest", "signals_from_probas"]
