"""Yahoo Finance price providers: underlying, local CEDEAR, and USD FX.

All three read the same chart endpoint (:mod:`.chart`); what differs is the
vendor symbol and the record type produced. A single fetch callable is shared,
so tests drive every mode from recorded bodies without any network.

**Symbol routing.** The vendor symbol is derived from the platform's own
vocabulary, never pasted from a feed:

* an underlying on B3 yields ``VALE3.SA``, on XETRA ``SIE.DE``, on LSE
  ``ULVR.L``; US venues carry no suffix; an ``UNKNOWN`` venue cannot be
  fetched (the ticker domain would be a guess);
* a CEDEAR local quote is the BYMA listing: ``AAPL.BA``;
* a currency pair is the cross: ``USDARS=X``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.core.logging import get_logger
from app.domain.vocabulary import UnderlyingMarket
from app.providers.base import (
    BarSnapshot,
    FxBar,
    FXDataProvider,
    FxSnapshot,
    LocalPriceDataProvider,
    ProviderError,
    UnderlyingDataProvider,
)
from app.providers.http import DEFAULT_USER_AGENT, FetchCallable, HttpFetcher
from app.providers.implementations.yahoo.chart import (
    BYMA_SUFFIX,
    CROSS_SUFFIX,
    chart_url,
    parse_chart,
)

logger = get_logger(__name__)

PROVIDER_NAME = "yahoo"

#: ``UnderlyingMarket ->`` Yahoo ticket suffix. Venues without a suffix map to
#: the empty string; the plain ticker is both quotespaces' name.
_MARKET_SUFFIXES: dict[UnderlyingMarket, str] = {
    UnderlyingMarket.NYSE: "",
    UnderlyingMarket.NYSE_AMERICAN: "",
    UnderlyingMarket.NYSE_ARCA: "",
    UnderlyingMarket.NASDAQ_GS: "",
    UnderlyingMarket.NASDAQ_GM: "",
    UnderlyingMarket.NASDAQ_CM: "",
    UnderlyingMarket.CBOE_BZX: "",
    UnderlyingMarket.OTC: "",
    UnderlyingMarket.B3: ".SA",
    UnderlyingMarket.XETRA: ".DE",
    UnderlyingMarket.LSE: ".L",
}


def vendor_symbol(symbol: str, market: UnderlyingMarket) -> str:
    """The Yahoo ticker for a platform symbol on a known venue.

    Raises:
        ProviderError: for an ``UNKNOWN`` market - the ticker domain would be
            a guess, and guessing a series is how false matches get stored.
    """
    suffix = _MARKET_SUFFIXES.get(market)
    if suffix is None:
        raise ProviderError(
            f"cannot route {symbol} to a Yahoo ticker: market {market.value} is UNKNOWN"
        )
    return f"{symbol}{suffix}"


class YahooUnderlyingProvider(UnderlyingDataProvider):
    """Daily OHLCV for a foreign underlying, in its own timezone."""

    name = PROVIDER_NAME

    def __init__(self, fetch: FetchCallable | None = None) -> None:
        """Inject a fetcher; tests serve a recorded body."""
        self._fetch: FetchCallable = fetch or HttpFetcher(user_agent=DEFAULT_USER_AGENT)

    def fetch_daily_bars(
        self,
        symbol: str,
        market: UnderlyingMarket,
        start: date,
        end: date,
    ) -> BarSnapshot:
        """Fetch and return a :class:`BarSnapshot` for the underlying."""
        vendor = vendor_symbol(symbol, market)
        url = chart_url(vendor, start, end)
        body = self._fetch(url)
        bars, rejected, _meta = parse_chart(
            body,
            provider=self.name,
            source_url=url,
            display_symbol=symbol,
        )
        logger.info(
            "provider.underlying_fetched",
            provider=self.name,
            symbol=symbol,
            bars=len(bars),
        )
        return BarSnapshot(
            provider=self.name,
            source_url=url,
            fetched_at=datetime.now(UTC),
            bars=bars,
            rejected=rejected,
        )


class YahooLocalPriceProvider(LocalPriceDataProvider):
    """Daily OHLCV for a CEDEAR as quoted on BYMA, in ARS."""

    name = PROVIDER_NAME

    def __init__(self, fetch: FetchCallable | None = None) -> None:
        """Inject a fetcher; tests serve a recorded body."""
        self._fetch: FetchCallable = fetch or HttpFetcher(user_agent=DEFAULT_USER_AGENT)

    def fetch_daily_bars(
        self,
        cedear_symbol: str,
        start: date,
        end: date,
    ) -> BarSnapshot:
        """Fetch and return the local series as a :class:`BarSnapshot`."""
        vendor = f"{cedear_symbol}{BYMA_SUFFIX}"
        url = chart_url(vendor, start, end)
        body = self._fetch(url)
        bars, rejected, _meta = parse_chart(
            body,
            provider=self.name,
            source_url=url,
            display_symbol=cedear_symbol,
        )
        logger.info(
            "provider.local_fetched",
            provider=self.name,
            symbol=cedear_symbol,
            bars=len(bars),
        )
        return BarSnapshot(
            provider=self.name,
            source_url=url,
            fetched_at=datetime.now(UTC),
            bars=bars,
            rejected=rejected,
        )


class YahooFxProvider(FXDataProvider):
    """Daily USD/ARS (or any ``XXXYYY=X`` pair) observations."""

    name = PROVIDER_NAME

    def __init__(self, fetch: FetchCallable | None = None) -> None:
        """Inject a fetcher; tests serve a recorded body."""
        self._fetch: FetchCallable = fetch or HttpFetcher(user_agent=DEFAULT_USER_AGENT)

    def fetch_daily(self, pair: str, start: date, end: date) -> FxSnapshot:
        """Fetch the cross and return an :class:`FxSnapshot`.

        The rate is ARS per unit of the other leg; ``currency`` on every bar is
        the currency the platform prices in, independent of what Yahoo records
        in ``meta``.
        """
        vendor = f"{pair}{CROSS_SUFFIX}"
        url = chart_url(vendor, start, end)
        body = self._fetch(url)
        bars, rejected, meta = parse_chart(
            body,
            provider=self.name,
            source_url=url,
            display_symbol=pair,
        )
        fx_bars = tuple(
            FxBar(
                pair=pair,
                market_date=bar.market_date,
                timestamp=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                currency="ARS",
                source=self.name,
            )
            for bar in bars
        )
        logger.info(
            "provider.fx_fetched",
            provider=self.name,
            pair=pair,
            bars=len(fx_bars),
            currency=meta.get("currency"),
        )
        return FxSnapshot(
            provider=self.name,
            source_url=url,
            fetched_at=datetime.now(UTC),
            bars=fx_bars,
            rejected=rejected,
        )


__all__ = [
    "PROVIDER_NAME",
    "YahooFxProvider",
    "YahooLocalPriceProvider",
    "YahooUnderlyingProvider",
    "vendor_symbol",
]
