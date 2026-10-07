"""FX feature definitions and computation (USDARS).

Returns, volatility, momentum, rolling change.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.core import FeatureDefinition, FeatureRegistry
from app.models.market_data import FxRate


def register_fx_features(registry: FeatureRegistry) -> None:
    """Register FX feature definitions."""
    family = "fx"
    features = [
        FeatureDefinition(
            name="fx_return_1d",
            family=family,
            description="1-day USDARS return",
            inputs=["fx_rate"],
            lookback_days=1,
        ),
        FeatureDefinition(
            name="fx_return_5d",
            family=family,
            description="5-day USDARS return",
            inputs=["fx_rate"],
            lookback_days=5,
        ),
        FeatureDefinition(
            name="fx_return_20d",
            family=family,
            description="20-day USDARS return",
            inputs=["fx_rate"],
            lookback_days=20,
        ),
        FeatureDefinition(
            name="fx_volatility_20d",
            family=family,
            description="20-day rolling volatility of FX rate",
            inputs=["fx_rate"],
            lookback_days=20,
        ),
        FeatureDefinition(
            name="fx_momentum_20d",
            family=family,
            description="20-day FX momentum",
            inputs=["fx_rate"],
            lookback_days=20,
        ),
        FeatureDefinition(
            name="fx_rolling_change_5d",
            family=family,
            description="5-day rolling change in FX",
            inputs=["fx_rate"],
            lookback_days=5,
        ),
    ]
    for f in features:
        registry.register(f)


def _fetch_fx_bars(
    session: Session, pair: str, as_of: datetime, lookback_days: int
) -> pd.DataFrame:
    from app.core.time import to_market_date

    market_date_limit = to_market_date(as_of)
    stmt = (
        select(FxRate)
        .where(FxRate.pair == pair)
        .where(FxRate.market_date < market_date_limit)
        .order_by(FxRate.market_date.desc())
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
            }
        )
    df = pd.DataFrame(data).sort_values("market_date").reset_index(drop=True)
    return df


def compute_fx_features(
    registry: FeatureRegistry,
    instrument_id: int,
    as_of: datetime,
    session: Session | None = None,
) -> dict[str, Decimal | float | None]:
    """Compute FX features (USDARS) for `as_of`."""
    default_none: dict[str, Decimal | float | None] = {
        "fx_return_1d": None,
        "fx_return_5d": None,
        "fx_return_20d": None,
        "fx_volatility_20d": None,
        "fx_momentum_20d": None,
        "fx_rolling_change_5d": None,
    }
    if session is None:
        return default_none
    df = _fetch_fx_bars(session, "USDARS", as_of, lookback_days=20)
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

    def rolling_vol(days: int) -> float | None:
        if len(closes) < days:
            return None
        returns = closes.pct_change().dropna()
        return float(returns.tail(days).std() * np.sqrt(252))

    def momentum(days: int) -> float | None:
        if len(closes) < days + 1:
            return None
        prev = closes.iloc[-(days + 1)]
        curr = closes.iloc[-1]
        return float(curr - prev)

    def rolling_change(days: int) -> float | None:
        if len(closes) < days + 1:
            return None
        prev = closes.iloc[-(days + 1)]
        curr = closes.iloc[-1]
        return float((curr - prev) / prev) if prev != 0 else None

    results = default_none.copy()
    results["fx_return_1d"] = return_n(1)
    results["fx_return_5d"] = return_n(5)
    results["fx_return_20d"] = return_n(20)
    results["fx_volatility_20d"] = rolling_vol(20)
    results["fx_momentum_20d"] = momentum(20)
    results["fx_rolling_change_5d"] = rolling_change(5)
    return results
