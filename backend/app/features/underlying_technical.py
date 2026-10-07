"""Underlying technical feature definitions and computation.

Same feature family as CEDEAR technical, computed on the underlying security.
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
from app.models.market_data import UnderlyingPriceBar


def register_underlying_features(registry: FeatureRegistry) -> None:
    """Register underlying technical feature definitions."""
    family = "underlying_technical"
    features = [
        FeatureDefinition(
            name="underlying_return_1d",
            family=family,
            description="1-day underlying return",
            inputs=["underlying_price_bar"],
            lookback_days=1,
        ),
        FeatureDefinition(
            name="underlying_return_5d",
            family=family,
            description="5-day underlying return",
            inputs=["underlying_price_bar"],
            lookback_days=5,
        ),
        FeatureDefinition(
            name="underlying_return_10d",
            family=family,
            description="10-day underlying return",
            inputs=["underlying_price_bar"],
            lookback_days=10,
        ),
        FeatureDefinition(
            name="underlying_return_20d",
            family=family,
            description="20-day underlying return",
            inputs=["underlying_price_bar"],
            lookback_days=20,
        ),
        FeatureDefinition(
            name="underlying_sma_5",
            family=family,
            description="5-day SMA",
            inputs=["underlying_price_bar"],
            lookback_days=5,
        ),
        FeatureDefinition(
            name="underlying_sma_10",
            family=family,
            description="10-day SMA",
            inputs=["underlying_price_bar"],
            lookback_days=10,
        ),
        FeatureDefinition(
            name="underlying_sma_20",
            family=family,
            description="20-day SMA",
            inputs=["underlying_price_bar"],
            lookback_days=20,
        ),
        FeatureDefinition(
            name="underlying_sma_50",
            family=family,
            description="50-day SMA",
            inputs=["underlying_price_bar"],
            lookback_days=50,
        ),
        FeatureDefinition(
            name="underlying_ema_10",
            family=family,
            description="10-day EMA",
            inputs=["underlying_price_bar"],
            lookback_days=10,
        ),
        FeatureDefinition(
            name="underlying_ema_20",
            family=family,
            description="20-day EMA",
            inputs=["underlying_price_bar"],
            lookback_days=20,
        ),
        FeatureDefinition(
            name="underlying_rsi_14",
            family=family,
            description="14-day RSI",
            inputs=["underlying_price_bar"],
            lookback_days=14,
        ),
        FeatureDefinition(
            name="underlying_macd",
            family=family,
            description="MACD line",
            inputs=["underlying_price_bar"],
            lookback_days=26,
        ),
        FeatureDefinition(
            name="underlying_macd_signal",
            family=family,
            description="MACD signal line",
            inputs=["underlying_price_bar"],
            lookback_days=26,
        ),
        FeatureDefinition(
            name="underlying_atr_14",
            family=family,
            description="14-day ATR",
            inputs=["underlying_price_bar"],
            lookback_days=14,
        ),
        FeatureDefinition(
            name="underlying_rolling_volatility_20d",
            family=family,
            description="20-day rolling volatility",
            inputs=["underlying_price_bar"],
            lookback_days=20,
        ),
        FeatureDefinition(
            name="underlying_volume_change_5d",
            family=family,
            description="5-day volume change ratio",
            inputs=["underlying_price_bar"],
            lookback_days=5,
        ),
    ]
    for f in features:
        registry.register(f)


def _fetch_underlying_bars(
    session: Session, instrument_id: int, as_of: datetime, lookback_days: int
) -> pd.DataFrame:
    from app.core.time import to_market_date

    market_date_limit = to_market_date(as_of)
    stmt = (
        select(UnderlyingPriceBar)
        .where(UnderlyingPriceBar.instrument_id == instrument_id)
        .where(UnderlyingPriceBar.market_date < market_date_limit)
        .order_by(UnderlyingPriceBar.market_date.desc())
        .limit(lookback_days + 50)
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
    df = pd.DataFrame(data).sort_values("market_date").reset_index(drop=True)
    return df


def compute_underlying_technical_features(
    registry: FeatureRegistry,
    instrument_id: int,
    as_of: datetime,
    session: Session | None = None,
) -> dict[str, Decimal | float | None]:
    """Compute underlying technical features for one instrument at `as_of`."""
    default_none: dict[str, Decimal | float | None] = {
        "underlying_return_1d": None,
        "underlying_return_5d": None,
        "underlying_return_10d": None,
        "underlying_return_20d": None,
        "underlying_sma_5": None,
        "underlying_sma_10": None,
        "underlying_sma_20": None,
        "underlying_sma_50": None,
        "underlying_ema_10": None,
        "underlying_ema_20": None,
        "underlying_rsi_14": None,
        "underlying_macd": None,
        "underlying_macd_signal": None,
        "underlying_atr_14": None,
        "underlying_rolling_volatility_20d": None,
        "underlying_volume_change_5d": None,
    }
    if session is None:
        return default_none
    df = _fetch_underlying_bars(session, instrument_id, as_of, lookback_days=50)
    if df.empty or len(df) < 2:
        return default_none
    closes = df["close"].dropna()
    if len(closes) < 2:
        return default_none

    def return_n(days: int) -> float | None:
        if len(closes) < days + 1:
            return None
        prev = closes.iloc[-(days + 1)]
        curr = closes.iloc[-1]
        return float((curr - prev) / prev) if prev != 0 else None

    def sma(days: int) -> float | None:
        if len(closes) < days:
            return None
        return float(closes.tail(days).mean())

    def ema(days: int) -> float | None:
        if len(closes) < days:
            return None
        return float(closes.tail(days).ewm(span=days, adjust=False).mean().iloc[-1])

    high_series = df["high"].dropna()
    low_series = df["low"].dropna()
    close_series = df["close"].dropna()

    results = default_none.copy()
    results["underlying_return_1d"] = return_n(1)
    results["underlying_return_5d"] = return_n(5)
    results["underlying_return_10d"] = return_n(10)
    results["underlying_return_20d"] = return_n(20)
    results["underlying_sma_5"] = sma(5)
    results["underlying_sma_10"] = sma(10)
    results["underlying_sma_20"] = sma(20)
    results["underlying_sma_50"] = sma(50)
    results["underlying_ema_10"] = ema(10)
    results["underlying_ema_20"] = ema(20)
    # Wilder's RSI; MACD signal is the EMA of the MACD line itself.
    results["underlying_rsi_14"] = rsi_wilder(closes, period=14)
    macd_line, macd_signal = macd_and_signal(closes)
    results["underlying_macd"] = macd_line
    results["underlying_macd_signal"] = macd_signal
    results["underlying_rolling_volatility_20d"] = rolling_volatility(closes, window=20)

    if len(high_series) >= 15 and len(low_series) >= 15 and len(close_series) >= 15:
        results["underlying_atr_14"] = atr_wilder(high_series, low_series, close_series, period=14)
    else:
        results["underlying_atr_14"] = None

    vol_series = df["volume"].dropna()
    if len(vol_series.dropna()) >= 6:
        prev = vol_series.tail(6).iloc[0]
        curr = vol_series.tail(1).iloc[0]
        results["underlying_volume_change_5d"] = float((curr - prev) / prev) if prev != 0 else None
    else:
        results["underlying_volume_change_5d"] = None
    return results
