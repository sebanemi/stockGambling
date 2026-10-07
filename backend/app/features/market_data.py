"""Generic market data reader for market-level features.

Adding a new market feature never changes the model interface; it only
registers a new key in the reader.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market_data import LocalPriceBar


class MarketDataReader:
    """Reads market-wide data series (S&P 500, Nasdaq, etc.).

    The interface is fixed: `read(series_key, market_date)` returns the
    observation for that date or `None`. New series are added by registering
    their key and data source, not by changing consumer code.
    """

    def __init__(self) -> None:
        """Initialise the market data reader."""
        self._series: dict[str, str] = {
            "sp500": "SPX",
            "nasdaq": "IXIC",
            "dow": "DJI",
            "russell_2000": "RUT",
            "vix": "VIX",
            "merval": "MERVAL",
        }

    def available_series(self) -> list[str]:
        """Return available series keys."""
        return list(self._series.keys())

    def read(
        self, series_key: str, market_date: date, session: Session | None = None
    ) -> Decimal | float | None:
        """Read a market observation for `market_date`."""
        if session is None:
            return None
        # Merval (BYMA index) mapped to a synthetic instrument symbol
        # For Phase 5, only Merval has a concrete DB source.
        if series_key == "merval":
            # Query LocalPriceBar for Merval index symbol "MERVAL"
            bar = session.scalar(
                select(LocalPriceBar)
                .where(LocalPriceBar.symbol == "MERVAL")
                .where(LocalPriceBar.market_date == market_date)
            )
            if bar is not None and bar.close is not None:
                return float(bar.close)
            return None
        # Other market series are not yet backed by a concrete table in Phase 5.
        return None


def get_market_data_reader() -> MarketDataReader:
    """Get the market data reader instance."""
    return MarketDataReader()
