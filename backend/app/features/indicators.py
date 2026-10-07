"""Shared technical-indicator implementations.

All helpers are pure functions over already-filtered price histories: the
caller is responsible for the as-of rule (only bars strictly before the
prediction instant reach these functions). Leakage prevention therefore lives
in the ``_fetch_*`` queries, never in the maths below.

Conventions (documented so Phase 6+ models are comparable):

* ``rsi_wilder``: Wilder's smoothing (RMA, ``alpha = 1/N``). Returns ``100.0``
  when average loss is zero and there was at least one gain, ``50.0`` on a
  perfectly flat series, ``None`` when fewer than ``period + 1`` closes exist.
* ``macd``: 12/26 EMA pair; the signal line is the 9-period EMA of the MACD
  *line* itself (not of the close), computed over the full MACD-line history
  available in the fetched window.
* ``atr_wilder``: Wilder's ATR (RMA of the true range), with the standard
  true-range definition ``max(h-l, |h-prev_close|, |l-prev_close|)``.
* ``rolling_volatility``: annualised stdev of simple returns, ``sqrt(252)``.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def rsi_wilder(closes: pd.Series[float], period: int = 14) -> float | None:
    """Wilder's RSI over ``closes`` (oldest-first)."""
    series = closes.dropna()
    if len(series) < period + 1:
        return None
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder's RMA == ewm(alpha=1/period, adjust=False); seed with SMA.
    avg_gain = float(gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().iloc[-1])
    avg_loss = float(loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().iloc[-1])
    if math.isnan(avg_gain) or math.isnan(avg_loss):
        return None
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def macd_and_signal(
    closes: pd.Series[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float | None, float | None]:
    """MACD line and signal line over ``closes`` (oldest-first)."""
    series = closes.dropna()
    if len(series) < slow:
        return None, None
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    # Signal needs enough MACD-line history; require at least `signal` points
    # after the slow-EMA warmup, i.e. slow + signal - 1 closes.
    if len(series) < slow + signal - 1:
        return float(macd_line.iloc[-1]), None
    signal_line = macd_line.ewm(span=signal, adjust=False).mean().iloc[-1]
    return float(macd_line.iloc[-1]), float(signal_line)


def atr_wilder(
    highs: pd.Series[float],
    lows: pd.Series[float],
    closes: pd.Series[float],
    period: int = 14,
) -> float | None:
    """Wilder's ATR over aligned high/low/close series (oldest-first)."""
    high = highs.dropna().reset_index(drop=True)
    low = lows.dropna().reset_index(drop=True)
    close = closes.dropna().reset_index(drop=True)
    n = min(len(high), len(low), len(close))
    if n < period + 1:
        return None
    high = high.tail(n).reset_index(drop=True)
    low = low.tail(n).reset_index(drop=True)
    close = close.tail(n).reset_index(drop=True)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = float(tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().iloc[-1])
    if math.isnan(atr):
        return None
    return atr


def rolling_volatility(closes: pd.Series[float], window: int = 20) -> float | None:
    """Annualised stdev of simple returns over the last ``window`` bars."""
    series = closes.dropna()
    if len(series) < window + 1:
        return None
    returns = series.pct_change().dropna().tail(window)
    if len(returns) < 2:
        return None
    std = float(returns.std(ddof=1))
    if math.isnan(std):
        return None
    return float(std * np.sqrt(252))


__all__ = ["atr_wilder", "macd_and_signal", "rolling_volatility", "rsi_wilder"]
