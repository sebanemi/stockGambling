"""Market feature definitions and computation.

S&P 500, Nasdaq, Dow, Russell 2000, VIX, sector ETF, Merval.
Read through a generic `market_data` reader.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.features.core import FeatureDefinition, FeatureRegistry


def register_market_features(registry: FeatureRegistry) -> None:
    """Register market feature definitions."""
    family = "market"
    features = [
        FeatureDefinition(
            name="sp500_return_1d",
            family=family,
            description="S&P 500 1-day return",
            inputs=["market_data"],
            lookback_days=1,
        ),
        FeatureDefinition(
            name="nasdaq_return_1d",
            family=family,
            description="Nasdaq 1-day return",
            inputs=["market_data"],
            lookback_days=1,
        ),
        FeatureDefinition(
            name="dow_return_1d",
            family=family,
            description="Dow Jones 1-day return",
            inputs=["market_data"],
            lookback_days=1,
        ),
        FeatureDefinition(
            name="russell_2000_return_1d",
            family=family,
            description="Russell 2000 1-day return",
            inputs=["market_data"],
            lookback_days=1,
        ),
        FeatureDefinition(
            name="vix_return_1d",
            family=family,
            description="VIX 1-day return",
            inputs=["market_data"],
            lookback_days=1,
        ),
        FeatureDefinition(
            name="merval_return_1d",
            family=family,
            description="Merval BYMA index 1-day return",
            inputs=["market_data"],
            lookback_days=1,
        ),
        FeatureDefinition(
            name="sector_etf_return_1d",
            family=family,
            description="Sector ETF 1-day return",
            inputs=["market_data"],
            lookback_days=1,
        ),
    ]
    for f in features:
        registry.register(f)


def compute_market_features(
    registry: FeatureRegistry,
    instrument_id: int,
    as_of: datetime,
    session: Session | None = None,
) -> dict[str, Decimal | float | None]:
    """Compute market-level features for `as_of`."""
    default_none: dict[str, Decimal | float | None] = {
        "sp500_return_1d": None,
        "nasdaq_return_1d": None,
        "dow_return_1d": None,
        "russell_2000_return_1d": None,
        "vix_return_1d": None,
        "merval_return_1d": None,
        "sector_etf_return_1d": None,
    }
    if session is None:
        return default_none
    from app.core.time import to_market_date

    market_date = to_market_date(as_of)
    results = default_none.copy()
    # Only Merval has a concrete DB backing in Phase 5.
    series_keys = {
        "merval_return_1d": "merval",
        "sp500_return_1d": "sp500",
        "nasdaq_return_1d": "nasdaq",
        "dow_return_1d": "dow",
        "russell_2000_return_1d": "russell_2000",
        "vix_return_1d": "vix",
        "sector_etf_return_1d": "sector_etf",
    }
    for feature_name, series_key in series_keys.items():
        if series_key == "merval" and session is not None:
            from sqlalchemy import select

            from app.models.market_data import LocalPriceBar

            current_bar = session.scalar(
                select(LocalPriceBar)
                .where(LocalPriceBar.symbol == "MERVAL")
                .where(LocalPriceBar.market_date == market_date)
            )
            prev_bar = session.scalar(
                select(LocalPriceBar)
                .where(LocalPriceBar.symbol == "MERVAL")
                .where(LocalPriceBar.market_date < market_date)
                .order_by(LocalPriceBar.market_date.desc())
                .limit(1)
            )
            if current_bar is not None and prev_bar is not None:
                if (
                    current_bar.close is not None
                    and prev_bar.close is not None
                    and prev_bar.close != 0
                ):
                    results[feature_name] = float(
                        (current_bar.close - prev_bar.close) / prev_bar.close
                    )
                else:
                    results[feature_name] = None
            else:
                results[feature_name] = None
        else:
            results[feature_name] = None
    return results
