"""Benchmark equity curves: buy-and-hold CEDEAR, cash, underlying-equivalent.

Benchmarks use the **same execution maths** as the strategy (same
slippage and commission on the entry fill), so the Phase 8 null-model
exit criterion holds exactly: an always-long run is indistinguishable
from buy-and-hold, an always-flat run from cash.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.backtesting.costs import BacktestCosts


def _as_price_series(prices: Any, name: str) -> NDArray[np.float64]:
    """Coerce and validate a strictly-positive finite price series."""
    series = np.asarray(prices, dtype=np.float64).reshape(-1)
    if series.size < 2:
        raise ValueError(f"{name} needs at least 2 prices, got {series.size}")
    if bool(np.any(~np.isfinite(series)) or np.any(series <= 0.0)):
        raise ValueError(f"{name} must be finite and strictly positive")
    return series


def buy_and_hold_curve(
    prices: NDArray[np.float64] | Any,
    *,
    initial_capital: float,
    costs: BacktestCosts | None = None,
) -> NDArray[np.float64]:
    """Buy at the first close (paying entry costs) and hold to the last bar.

    Args:
        prices: CEDEAR closes in ARS, oldest first.
        initial_capital: Starting cash in ARS (must be positive).
        costs: Cost model for the single entry fill.

    Returns:
        Equity per bar: ``initial_capital`` at bar 0 (cash before the entry
        fill), then ``shares * price[i]`` where ``shares`` is what the
        initial capital bought after entry commission and slippage. The
        first point matches the strategy curve so an always-long run is
        exactly the benchmark; entry friction shows from bar 1 onward.
    """
    series = _as_price_series(prices, "prices")
    if initial_capital <= 0.0 or not np.isfinite(initial_capital):
        raise ValueError(f"initial_capital must be finite and positive, got {initial_capital}")
    model = costs or BacktestCosts()
    entry_price = model.buy_execution_price(float(series[0]))
    gross = float(initial_capital)
    fee = model.commission_on(gross)
    shares = (gross - fee) / entry_price
    curve = (shares * series).astype(np.float64)
    curve[0] = float(initial_capital)
    return curve


def cash_curve(initial_capital: float, n_bars: int) -> NDArray[np.float64]:
    """Flat cash benchmark: no trade, no costs, constant equity.

    Args:
        initial_capital: Starting (and constant) cash.
        n_bars: Number of equity points (same length as the price series).

    Returns:
        A constant equity curve.
    """
    if initial_capital <= 0.0 or not np.isfinite(initial_capital):
        raise ValueError(f"initial_capital must be finite and positive, got {initial_capital}")
    if n_bars < 2:
        raise ValueError(f"Need at least 2 bars, got {n_bars}")
    return np.full(n_bars, float(initial_capital), dtype=np.float64)


def underlying_equivalent_curve(
    underlying_prices: NDArray[np.float64] | Any,
    *,
    initial_capital: float,
    costs: BacktestCosts | None = None,
) -> NDArray[np.float64]:
    """Buy-and-hold the underlying with the same notional (optional benchmark).

    The notional is the same number - meant for side-by-side inspection, not
    for mixing currencies: the returned curve is in the underlying's own
    currency and must be reported next to, never blended with, the ARS curve.
    """
    series = _as_price_series(underlying_prices, "underlying_prices")
    return buy_and_hold_curve(series, initial_capital=initial_capital, costs=costs)


__all__ = ["buy_and_hold_curve", "cash_curve", "underlying_equivalent_curve"]
