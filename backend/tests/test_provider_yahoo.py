"""Yahoo Finance chart adapter tests, driven by recorded bodies.

The fixtures are synthetic but shaped exactly like the live endpoint: aware
session-start timestamps in the exchange's local wall clock, ``null``/``NaN``
holes where a trade did not happen, and a ``chart.error`` for a delisted
symbol. No network is ever touched.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.vocabulary import UnderlyingMarket
from app.providers.base import ProviderError
from app.providers.http import FetchCallable
from app.providers.implementations.yahoo import (
    YahooFxProvider,
    YahooLocalPriceProvider,
    YahooUnderlyingProvider,
)
from app.providers.implementations.yahoo.chart import (
    BYMA_SUFFIX,
    CROSS_SUFFIX,
    chart_url,
    parse_chart,
)
from app.providers.implementations.yahoo.providers import vendor_symbol

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo"

START = date(2026, 7, 10)
END = date(2026, 7, 17)


def serve(body: bytes) -> FetchCallable:
    """Build a fetcher that returns ``body`` regardless of the requested URL."""
    return lambda _url: body


def load(name: str) -> bytes:
    """Read a recorded chart body from the fixture directory."""
    return (FIXTURES / name).read_bytes()


class TestUnderlyingProvider:
    """The foreign-series mode: market dates in the venue's own timezone."""

    def test_parses_us_bars_into_us_market_dates(self) -> None:
        """A 09:30 America/New_York session must keep its NY date, not the UTC one."""
        snapshot = YahooUnderlyingProvider(fetch=serve(load("aapl.json"))).fetch_daily_bars(
            "AAPL", UnderlyingMarket.NASDAQ_GS, START, END
        )

        assert snapshot.provider == "yahoo"
        assert snapshot.source_url == chart_url("AAPL", START, END)
        assert len(snapshot.bars) == 5
        assert snapshot.bars[0].market_date == date(2026, 7, 13)

    def test_every_bar_carries_the_full_vocabulary(self) -> None:
        """The provider-neutral frame (docs/data-providers.md) is preserved."""
        snapshot = YahooLocalPriceProvider(fetch=serve(load("aapl.ba.json"))).fetch_daily_bars(
            "AAPL", START, END
        )
        bar = snapshot.bars[2]

        assert bar.symbol == "AAPL"
        assert bar.market_date == date(2026, 7, 15)
        assert bar.open == Decimal("216900")
        assert bar.high == Decimal("217800")
        assert bar.low == Decimal("214800")
        assert bar.close == Decimal("216400")
        assert bar.volume == 404000
        assert bar.currency == "ARS"
        assert bar.source == "yahoo"
        assert bar.timestamp.tzinfo is not None
        assert bar.adjusted_close is None
        assert bar.traded_value is None
        assert bar.trades is None

    def test_timestamps_are_aware_utc_instants(self) -> None:
        """The stored instant is UTC-square, not a naive local wall clock."""
        snapshot = YahooUnderlyingProvider(fetch=serve(load("aapl.json"))).fetch_daily_bars(
            "AAPL", UnderlyingMarket.NASDAQ_GS, START, END
        )
        first = snapshot.bars[0]
        # 09:30 America/New_York (EDT = UTC-4) on 2026-07-13.
        assert first.timestamp == datetime(2026, 7, 13, 13, 30, tzinfo=UTC)
        assert first.timestamp.utcoffset() is not None

    def test_unknown_market_cannot_be_routed(self) -> None:
        """An UNKNOWN venue is refused: the ticker domain would be a guess."""
        with pytest.raises(ProviderError, match="UNKNOWN"):
            YahooUnderlyingProvider().fetch_daily_bars("SOME", UnderlyingMarket.UNKNOWN, START, END)

    def test_vendor_symbol_appendices(self) -> None:
        """Suffix routing follows the documented vendor vocabulary."""
        assert vendor_symbol("VALE3", UnderlyingMarket.B3) == "VALE3.SA"
        assert vendor_symbol("SIE", UnderlyingMarket.XETRA) == "SIE.DE"
        assert vendor_symbol("ULVR", UnderlyingMarket.LSE) == "ULVR.L"
        assert vendor_symbol("AAPL", UnderlyingMarket.NASDAQ_GS) == "AAPL"


class TestLocalProvider:
    """The BYMA mode: local listing, ARS, Buenos Aires calendar."""

    def test_local_symbol_gets_the_byma_suffix(self) -> None:
        """The CEDEAR quote is the .BA listing, and the label stays the symbol."""
        seen: list[str] = []

        def capture(url: str) -> bytes:
            seen.append(url)
            return load("aapl.ba.json")

        provider = YahooLocalPriceProvider(fetch=capture)
        snapshot = provider.fetch_daily_bars("AAPL", START, END)
        assert seen[0] == chart_url("AAPL.BA", START, END)
        assert all(bar.symbol == "AAPL" for bar in snapshot.bars)


class TestFxProvider:
    """The USD/ARS mode, with the rate labelled in the platform's currency."""

    def test_bars_carry_the_pair_and_ars_currency(self) -> None:
        """Rate bars are pair-labelled and priced in ARS, not in Yahoo's meta."""
        snapshot = YahooFxProvider(fetch=serve(load("usdars.json"))).fetch_daily(
            "USDARS", START, END
        )
        assert len(snapshot.bars) == 5
        bar = snapshot.bars[3]
        assert bar.pair == "USDARS"
        assert bar.market_date == date(2026, 7, 16)
        assert bar.close == Decimal("1109.6")
        assert bar.currency == "ARS"
        assert bar.source == "yahoo"


class TestQuietAndBrokenPayloads:
    """Holes and layout changes must be honest, never guessed."""

    def test_no_trade_slots_are_rejected_not_zeroed(self) -> None:
        """A quiet calendar slot becomes a rejection, not a 0.0 bar."""
        snapshot = YahooUnderlyingProvider(fetch=serve(load("quiet.json"))).fetch_daily_bars(
            "AAPL", UnderlyingMarket.NASDAQ_GS, START, END
        )
        assert len(snapshot.bars) == 2
        assert len(snapshot.rejected) == 1
        assert snapshot.rejected[0].reason == "no_trade_slot"

    def test_misaligned_arrays_abort(self) -> None:
        """A layout change must raise, never zip silently."""
        with pytest.raises(ProviderError, match="misaligned"):
            parse_chart(
                load("misaligned.json"),
                provider="yahoo",
                source_url="https://chart",
                display_symbol="AAPL",
            )

    def test_chart_error_is_a_provider_error(self) -> None:
        """Yahoo's own error (delisted symbol) must surface as such."""
        with pytest.raises(ProviderError, match="chart error"):
            YahooUnderlyingProvider(fetch=serve(load("error.json"))).fetch_daily_bars(
                "NPWR", UnderlyingMarket.OTC, START, END
            )

    def test_non_json_is_a_provider_error(self) -> None:
        """A CAPTCHA page is a structural failure, not an empty universe."""
        with pytest.raises(ProviderError, match="did not return JSON"):
            YahooUnderlyingProvider(fetch=serve(b"<html>rate limited</html>")).fetch_daily_bars(
                "AAPL", UnderlyingMarket.NASDAQ_GS, START, END
            )


class TestSuffixConstants:
    """The wire suffixes that route platform symbols to Yahoo tickers."""

    def test_suffixes_are_the_documented_wire_forms(self) -> None:
        """BYMA listings and crosses carry the expected suffixes."""
        assert BYMA_SUFFIX == ".BA"
        assert CROSS_SUFFIX == "=X"
