"""CEDEAR technical feature definitions and computation.

Returns, SMA, EMA, RSI, MACD, ATR, rolling volatility, volume features.
All computed from stored `LocalPriceBar` data only.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.core import FeatureDefinition, FeatureRegistry
from app.features.indicators import (
    atr_wilder,
    macd_and_signal,
    rolling_volatility,
    rsi_wilder,
)
from app.models.market_data import LocalPriceBar


def register_cedear_features(registry: FeatureRegistry) -> None:
    """Register CEDEAR technical feature definitions."""
    family = "cedear_technical"
    features = [
        FeatureDefinition(
            name="cedear_return_1d",
            family=family,
            description="1-day CEDEAR return: (close_t - close_t-1) / close_t-1",
            inputs=["local_price_bar"],
            lookback_days=1,
        ),
        FeatureDefinition(
            name="cedear_return_5d",
            family=family,
            description="5-day CEDEAR return",
            inputs=["local_price_bar"],
            lookback_days=5,
        ),
        FeatureDefinition(
            name="cedear_return_10d",
            family=family,
            description="10-day CEDEAR return",
            inputs=["local_price_bar"],
            lookback_days=10,
        ),
        FeatureDefinition(
            name="cedear_return_20d",
            family=family,
            description="20-day CEDEAR return",
            inputs=["local_price_bar"],
            lookback_days=20,
        ),
        FeatureDefinition(
            name="cedear_sma_5",
            family=family,
            description="5-day simple moving average of CEDEAR close",
            inputs=["local_price_bar"],
            lookback_days=5,
        ),
        FeatureDefinition(
            name="cedear_sma_10",
            family=family,
            description="10-day SMA",
            inputs=["local_price_bar"],
            lookback_days=10,
        ),
        FeatureDefinition(
            name="cedear_sma_20",
            family=family,
            description="20-day SMA",
            inputs=["local_price_bar"],
            lookback_days=20,
        ),
        FeatureDefinition(
            name="cedear_sma_50",
            family=family,
            description="50-day SMA",
            inputs=["local_price_bar"],
            lookback_days=50,
        ),
        FeatureDefinition(
            name="cedear_ema_10",
            family=family,
            description="10-day exponential moving average",
            inputs=["local_price_bar"],
            lookback_days=10,
        ),
        FeatureDefinition(
            name="cedear_ema_20",
            family=family,
            description="20-day EMA",
            inputs=["local_price_bar"],
            lookback_days=20,
        ),
        FeatureDefinition(
            name="cedear_rsi_14",
            family=family,
            description="14-day Relative Strength Index",
            inputs=["local_price_bar"],
            lookback_days=14,
        ),
        FeatureDefinition(
            name="cedear_macd",
            family=family,
            description="MACD line (12-day EMA - 26-day EMA)",
            inputs=["local_price_bar"],
            lookback_days=26,
        ),
        FeatureDefinition(
            name="cedear_macd_signal",
            family=family,
            description="MACD signal line (9-day EMA of MACD)",
            inputs=["local_price_bar"],
            lookback_days=26,
        ),
        FeatureDefinition(
            name="cedear_atr_14",
            family=family,
            description="14-day Average True Range",
            inputs=["local_price_bar"],
            lookback_days=14,
        ),
        FeatureDefinition(
            name="cedear_rolling_volatility_20d",
            family=family,
            description="20-day rolling volatility of CEDEAR close",
            inputs=["local_price_bar"],
            lookback_days=20,
        ),
        FeatureDefinition(
            name="cedear_volume_change_5d",
            family=family,
            description="5-day volume change ratio",
            inputs=["local_price_bar"],
            lookback_days=5,
        ),
        FeatureDefinition(
            name="cedear_volume_ratio_5d",
            family=family,
            description="5-day volume ratio vs 20-day average",
            inputs=["local_price_bar"],
            lookback_days=20,
        ),
    ]
    for f in features:
        registry.register(f)


def _fetch_local_bars(
    session: Session, instrument_id: int, as_of: datetime, lookback_days: int
) -> pd.DataFrame:
    """Fetch local price bars before `as_of` with sufficient history."""
    from app.core.time import to_market_date

    market_date_limit = to_market_date(as_of)
    # Fetch bars with enough history for the lookback
    stmt = (
        select(LocalPriceBar)
        .where(LocalPriceBar.instrument_id == instrument_id)
        .where(LocalPriceBar.market_date < market_date_limit)
        .order_by(LocalPriceBar.market_date.desc())
        .limit(lookback_days + 50)  # buffer for alignment
    )
    rows = session.scalars(stmt).all()
    if not rows:
        return pd.DataFrame()
    data = []
    for row in rows:
        data.append(
            {
                "market_date": row.market_date,
                "close": float(row.close) if row.close is not None else np.nan,
                "volume": int(row.volume) if row.volume is not None else np.nan,
                "high": float(row.high) if row.high is not None else np.nan,
                "low": float(row.low) if row.low is not None else np.nan,
            }
        )
    df = pd.DataFrame(data)
    df = df.sort_values("market_date").reset_index(drop=True)
    return df


def compute_cedear_technical_features(
    registry: FeatureRegistry,
    instrument_id: int,
    as_of: datetime,
    session: Session | None = None,
) -> dict[str, Decimal | float | None]:
    """Compute CEDEAR technical features for one instrument at `as_of`."""
    results: dict[str, Decimal | float | None] = {}

    # If no session provided, try to get one from the registry or create temporary
    # For this phase, require an explicit session.
    if session is None:
        # Return stubs if no session (backward compatible)
        return {
            "cedear_return_1d": None,
            "cedear_return_5d": None,
            "cedear_return_10d": None,
            "cedear_return_20d": None,
            "cedear_sma_5": None,
            "cedear_sma_10": None,
            "cedear_sma_20": None,
            "cedear_sma_50": None,
            "cedear_ema_10": None,
            "cedear_ema_20": None,
            "cedear_rsi_14": None,
            "cedear_macd": None,
            "cedear_macd_signal": None,
            "cedear_atr_14": None,
            "cedear_rolling_volatility_20d": None,
            "cedear_volume_change_5d": None,
            "cedear_volume_ratio_5d": None,
        }

    # Fetch sufficient history (max lookback is 50 days for SMA)
    df = _fetch_local_bars(session, instrument_id, as_of, lookback_days=50)
    if df.empty or len(df) < 2:
        return {
            "cedear_return_1d": None,
            "cedear_return_5d": None,
            "cedear_return_10d": None,
            "cedear_return_20d": None,
            "cedear_sma_5": None,
            "cedear_sma_10": None,
            "cedear_sma_20": None,
            "cedear_sma_50": None,
            "cedear_ema_10": None,
            "cedear_ema_20": None,
            "cedear_rsi_14": None,
            "cedear_macd": None,
            "cedear_macd_signal": None,
            "cedear_atr_14": None,
            "cedear_rolling_volatility_20d": None,
            "cedear_volume_change_5d": None,
            "cedear_volume_ratio_5d": None,
        }

    closes = df["close"].dropna()
    if len(closes) < 2:
        return {
            "cedear_return_1d": None,
            "cedear_return_5d": None,
            "cedear_return_10d": None,
            "cedear_return_20d": None,
            "cedear_sma_5": None,
            "cedear_sma_10": None,
            "cedear_sma_20": None,
            "cedear_sma_50": None,
            "cedear_ema_10": None,
            "cedear_ema_20": None,
            "cedear_rsi_14": None,
            "cedear_macd": None,
            "cedear_macd_signal": None,
            "cedear_atr_14": None,
            "cedear_rolling_volatility_20d": None,
            "cedear_volume_change_5d": None,
            "cedear_volume_ratio_5d": None,
        }

    # Basic returns
    def return_n(days: int) -> float | None:
        if len(closes) < days + 1:
            return None
        prev = closes.iloc[-(days + 1)]
        curr = closes.iloc[-1]
        return float((curr - prev) / prev) if prev != 0 else None

    results["cedear_return_1d"] = return_n(1)
    results["cedear_return_5d"] = return_n(5)
    results["cedear_return_10d"] = return_n(10)
    results["cedear_return_20d"] = return_n(20)

    # SMA
    def sma(days: int) -> float | None:
        if len(closes) < days:
            return None
        return float(closes.tail(days).mean())

    results["cedear_sma_5"] = sma(5)
    results["cedear_sma_10"] = sma(10)
    results["cedear_sma_20"] = sma(20)
    results["cedear_sma_50"] = sma(50)

    # EMA
    def ema(days: int) -> float | None:
        if len(closes) < days:
            return None
        return float(closes.tail(days).ewm(span=days, adjust=False).mean().iloc[-1])

    results["cedear_ema_10"] = ema(10)
    results["cedear_ema_20"] = ema(20)

    # RSI 14 (Wilder's smoothing; see app.features.indicators).
    results["cedear_rsi_14"] = rsi_wilder(closes, period=14)

    # MACD: signal is the 9-period EMA of the MACD line itself.
    macd_line, macd_signal = macd_and_signal(closes)
    results["cedear_macd"] = macd_line
    results["cedear_macd_signal"] = macd_signal

    # ATR 14 (Wilder's smoothing over the standard true range).
    high_series = df["high"].dropna()
    low_series = df["low"].dropna()
    close_series = df["close"].dropna()
    if len(high_series) >= 15 and len(low_series) >= 15 and len(close_series) >= 15:
        results["cedear_atr_14"] = atr_wilder(high_series, low_series, close_series, period=14)
    else:
        results["cedear_atr_14"] = None

    # Rolling volatility 20d (annualised, sqrt(252)).
    results["cedear_rolling_volatility_20d"] = rolling_volatility(closes, window=20)

    # Volume change 5d
    def volume_change(series_vol: pd.Series[float], days: int) -> float | None:
        if len(series_vol.dropna()) < days + 1:
            return None
        prev = series_vol.dropna().tail(days + 1).iloc[0]
        curr = series_vol.dropna().tail(1).iloc[0]
        return float((curr - prev) / prev) if prev != 0 else None

    vol_series = df["volume"].dropna()
    results["cedear_volume_change_5d"] = volume_change(vol_series, 5)

    # Volume ratio 5d vs 20d
    def volume_ratio(series_vol: pd.Series[float], short: int, long_: int) -> float | None:
        if len(series_vol.dropna()) < long_:
            return None
        short_avg = series_vol.dropna().tail(short).mean()
        long_avg = series_vol.dropna().tail(long_).mean()
        return float(short_avg / long_avg) if long_avg != 0 else None

    results["cedear_volume_ratio_5d"] = volume_ratio(vol_series, 5, 20)

    # Initialize missing results
    for name in [
        "cedear_return_1d",
        "cedear_return_5d",
        "cedear_return_10d",
        "cedear_return_20d",
        "cedear_sma_5",
        "cedear_sma_10",
        "cedear_sma_20",
        "cedear_sma_50",
        "cedear_ema_10",
        "cedear_ema_20",
        "cedear_rsi_14",
        "cedear_macd",
        "cedear_macd_signal",
        "cedear_atr_14",
        "cedear_rolling_volatility_20d",
        "cedear_volume_change_5d",
        "cedear_volume_ratio_5d",
    ]:
        if name not in results:
            results[name] = None

    return results
