"""Backtest performance metrics, computed separately per currency.

ARS and USD-adjusted returns are **never mixed** (methodology section 7):
the caller converts the ARS equity curve with :func:`to_usd_curve` and
scores each curve with :func:`backtest_metrics` independently. Undefined
quantities degrade to ``None`` - never to a fabricated number:

* volatility/Sharpe need at least two per-step returns and nonzero variance.
* win rate / average trade / profit factor need closed round-trip trades.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray


def to_usd_curve(
    equity_ars: NDArray[np.float64] | Any, fx_ars_per_usd: NDArray[np.float64] | Any
) -> NDArray[np.float64]:
    """Convert an ARS equity curve to USD using the FX series.

    Args:
        equity_ars: Portfolio value per bar, in ARS.
        fx_ars_per_usd: ARS per USD per bar (same length, strictly positive).

    Returns:
        The equity curve expressed in USD.
    """
    equity = np.asarray(equity_ars, dtype=np.float64).reshape(-1)
    fx = np.asarray(fx_ars_per_usd, dtype=np.float64).reshape(-1)
    if equity.shape != fx.shape:
        raise ValueError(f"Shape mismatch: {equity.shape} vs {fx.shape}")
    if equity.size == 0:
        raise ValueError("Cannot convert an empty equity curve")
    if bool(np.any(~np.isfinite(fx)) or np.any(fx <= 0.0)):
        raise ValueError("FX observations must be finite and strictly positive")
    return (equity / fx).astype(np.float64)


def _max_drawdown(equity: NDArray[np.float64]) -> float:
    """Largest peak-to-trough fraction (0.0 when the curve never falls)."""
    peak = np.maximum.accumulate(equity)
    drawdown = np.where(peak > 0.0, (peak - equity) / peak, 0.0)
    return float(np.max(drawdown))


def backtest_metrics(
    equity_curve: NDArray[np.float64] | Any,
    *,
    trade_pnls: NDArray[np.float64] | Any,
    total_costs: float,
    trading_days_per_year: int = 252,
) -> dict[str, float | None]:
    """Score one equity curve plus its closed round-trip trades.

    Args:
        equity_curve: Portfolio value per bar (one currency only).
        trade_pnls: Net P&L per closed round-trip trade (same currency).
        total_costs: Commissions paid over the run (same currency).
        trading_days_per_year: Annualisation factor (252 BYMA sessions).

    Returns:
        ``total_return``, ``annualised_return``, ``volatility`` (annualised
        std of per-step simple returns), ``sharpe`` (risk-free 0),
        ``max_drawdown``, ``win_rate``, ``trade_count``, ``average_trade``,
        ``profit_factor`` and ``total_costs``. Entries that need data the
        run does not have (no trades, flat curve) are ``None``.
    """
    equity = np.asarray(equity_curve, dtype=np.float64).reshape(-1)
    pnls = np.asarray(trade_pnls, dtype=np.float64).reshape(-1)
    if equity.size < 2:
        raise ValueError(f"Need at least 2 equity points, got {equity.size}")
    if bool(np.any(~np.isfinite(equity))):
        raise ValueError("Equity curve must be finite")
    if equity[0] <= 0.0:
        raise ValueError(f"Initial equity must be positive, got {equity[0]}")
    if total_costs < 0.0 or not np.isfinite(total_costs):
        raise ValueError(f"total_costs must be finite and non-negative, got {total_costs}")

    total_return = float(equity[-1] / equity[0] - 1.0)
    n_steps = equity.size - 1
    annualised_return = float((equity[-1] / equity[0]) ** (trading_days_per_year / n_steps) - 1.0)

    step_returns = equity[1:] / equity[:-1] - 1.0
    volatility: float | None = None
    sharpe: float | None = None
    if step_returns.size >= 2:
        std = float(np.std(step_returns, ddof=1))
        if std > 0.0:
            volatility = float(std * np.sqrt(trading_days_per_year))
            sharpe = float(np.mean(step_returns) / std * np.sqrt(trading_days_per_year))

    n_trades = int(pnls.size)
    win_rate: float | None = None
    average_trade: float | None = None
    profit_factor: float | None = None
    if n_trades > 0:
        win_rate = float(np.mean(pnls > 0.0))
        average_trade = float(np.mean(pnls))
        gross_profit = float(np.sum(pnls[pnls > 0.0]))
        gross_loss = float(-np.sum(pnls[pnls < 0.0]))
        if gross_loss > 0.0:
            profit_factor = float(gross_profit / gross_loss)

    return {
        "total_return": total_return,
        "annualised_return": annualised_return,
        "volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": _max_drawdown(equity),
        "win_rate": win_rate,
        "trade_count": float(n_trades),
        "average_trade": average_trade,
        "profit_factor": profit_factor,
        "total_costs": float(total_costs),
    }


__all__ = ["backtest_metrics", "to_usd_curve"]
