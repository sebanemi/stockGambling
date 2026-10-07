"""Caja de Valores provider tests, driven by a recorded page.

The fixture is a verbatim slice of the live page, including the markup defects
the adapter must survive: line-broken headers, nested links and inline ``style``
attributes inside cells, non-breaking spaces padding amount cells, and a row
whose underlying ticker differs from its BYMA symbol.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.vocabulary import InstrumentType, ProgramStatus, UnderlyingMarket
from app.providers.base import CedearRecord, CedearSnapshot, ProviderError
from app.providers.http import FetchCallable
from app.providers.implementations.cajadevalores import (
    CEDEARS_URL,
    CajaDeValoresCedearProvider,
    table_kind,
)
from app.providers.implementations.cajadevalores.provider import clean_isin
from app.providers.implementations.html_tables import extract_tables

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).parent / "fixtures" / "cajadevalores" / "cedears.html"


def serve(body: bytes) -> FetchCallable:
    """Build a fetcher that returns ``body`` regardless of the requested URL."""
    return lambda _url: body


@pytest.fixture
def snapshot() -> CedearSnapshot:
    """Parse the recorded page through a fake transport."""
    return CajaDeValoresCedearProvider(fetch=serve(FIXTURE.read_bytes())).fetch()


@pytest.fixture
def by_symbol(snapshot: CedearSnapshot) -> dict[str, CedearRecord]:
    """Index the snapshot's records by symbol for readable assertions."""
    return {record.symbol: record for record in snapshot.records}


class TestSnapshotShape:
    """Provenance and the refusal to report an empty universe."""

    def test_provider_name_and_url(self, snapshot: CedearSnapshot) -> None:
        """Every record must be traceable to where it came from."""
        assert snapshot.provider == "cajadevalores"
        assert snapshot.source_url == CEDEARS_URL

    def test_both_tables_are_parsed(self, snapshot: CedearSnapshot) -> None:
        """The ETF and share tables are separate and both are needed.

        Reading only the first table would silently drop every share CEDEAR.
        """
        assert len(snapshot.records) == 18
        assert {r.instrument_type for r in snapshot.records} == {
            InstrumentType.ETF,
            InstrumentType.STOCK,
        }

    def test_no_table_found_is_an_error(self) -> None:
        """A layout change must abort rather than wipe the universe."""
        provider = CajaDeValoresCedearProvider(fetch=serve(b"<html>maintenance</html>"))
        with pytest.raises(ProviderError, match="tabla-cedears"):
            provider.fetch()

    def test_recognised_tables_with_no_rows_is_an_error(self) -> None:
        """An empty table means the page changed, not that the market emptied."""
        page = """
        <table class="tabla-cedears">
          <tr><th>CEDEAR de ETF</th><th>Símbolo BYMA</th>
              <th>Ticker en Mercado de Origen</th><th>Mercado de Origen</th>
              <th>Ratio CEDEARs / valor subyacente</th></tr>
        </table>
        """
        provider = CajaDeValoresCedearProvider(fetch=serve(page.encode()))
        with pytest.raises(ProviderError, match="refusing to report an empty universe"):
            provider.fetch()


class TestColumnLookup:
    """Columns are located by header name.

    The two tables spell some headers differently and the markup breaks them
    across lines, so a positional read would assign ratios to the wrong column.
    """

    def test_missing_ratio_column_is_an_error(self) -> None:
        """A positional fallback would assign ratios to the wrong column."""
        page = """
        <table class="tabla-cedears">
          <tr><th>CEDEAR de ETF</th><th>Símbolo BYMA</th>
              <th>Ticker en Mercado de Origen</th><th>Mercado de Origen</th></tr>
          <tr><td>SPY</td><td>SPY</td><td>SPY</td><td>NYSE</td></tr>
        </table>
        """
        provider = CajaDeValoresCedearProvider(fetch=serve(page.encode()))
        with pytest.raises(ProviderError, match="ratio"):
            provider.fetch()

    def test_renamed_table_kind_is_skipped_not_guessed(self) -> None:
        """An unrecognised first header must not be read as a data column."""
        page = """
        <table class="tabla-cedears">
          <tr><th>CEDEAR de Bonos</th><th>Símbolo BYMA</th>
              <th>Ticker en Mercado de Origen</th><th>Mercado de Origen</th>
              <th>Ratio CEDEARs / valor subyacente</th></tr>
          <tr><td>Bono</td><td>ABC</td><td>ABC</td><td>NYSE</td><td>1:1</td></tr>
        </table>
        """
        provider = CajaDeValoresCedearProvider(fetch=serve(page.encode()))
        with pytest.raises(ProviderError):
            provider.fetch()

    def test_table_kind_reads_the_first_header(self) -> None:
        """The first cell names the program kind, not a data column."""
        tables = extract_tables(FIXTURE.read_text(encoding="utf-8"), "tabla-cedears")
        assert table_kind(tables[0]) is InstrumentType.ETF
        assert table_kind(tables[1]) is InstrumentType.STOCK

    def test_table_kind_of_a_table_without_headers(self) -> None:
        """A headerless table is unidentifiable rather than ETF by default."""
        page = '<table class="tabla-cedears"><tr><td>x</td></tr></table>'
        assert table_kind(extract_tables(page, "tabla-cedears")[0]) is None


class TestRatioParsing:
    """Ratios are ``N:D`` fractions published in the table."""

    def test_integral_ratio(self, by_symbol: dict[str, CedearRecord]) -> None:
        """``60:1`` means 60 CEDEARs per one ETF share."""
        assert by_symbol["SPY"].ratio == Decimal(60)

    def test_fractional_ratio(self, by_symbol: dict[str, CedearRecord]) -> None:
        """``1:5`` is 0.2, not 1.

        Reading it as 1 would inflate the theoretical price of ``KEEL`` by a
        factor of five.
        """
        assert by_symbol["KEEL"].ratio == Decimal("0.2")

    def test_one_to_one(self, by_symbol: dict[str, CedearRecord]) -> None:
        """``1:1`` programs are not exotic; ``F`` is one."""
        assert by_symbol["F"].ratio == Decimal(1)

    def test_original_text_is_retained(self, by_symbol: dict[str, CedearRecord]) -> None:
        """The published form is kept for audit alongside the parsed value."""
        assert by_symbol["SPY"].extra["ratio_raw"] == "60:1"


class TestUnderlyingMarket:
    """The venue column is mapped, not guessed."""

    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            ("SPY", UnderlyingMarket.NYSE_ARCA),
            ("F", UnderlyingMarket.NYSE),
            ("UAL", UnderlyingMarket.NASDAQ_GS),
            ("VALE3", UnderlyingMarket.B3),
        ],
    )
    def test_known_venues(
        self,
        symbol: str,
        expected: UnderlyingMarket,
        by_symbol: dict[str, CedearRecord],
    ) -> None:
        """Each published venue spelling resolves to its canonical code."""
        assert by_symbol[symbol].underlying_market is expected

    def test_venue_wrapped_in_a_span_is_still_read(
        self, by_symbol: dict[str, CedearRecord]
    ) -> None:
        """``ARKK``'s venue cell is wrapped in an inline ``style`` element.

        Reading the raw cell would keep the attribute text and lose the venue.
        """
        assert by_symbol["ARKK"].underlying_market is UnderlyingMarket.NYSE_ARCA


class TestIdentifiers:
    """ISINs are validated; the BYMA symbol is kept even when it differs."""

    def test_cedear_and_underlying_isin_differ_by_prefix(
        self, by_symbol: dict[str, CedearRecord]
    ) -> None:
        """A CEDEAR ISIN identifies the wrapper, not the underlying."""
        record = by_symbol["SPY"]
        assert record.isin == "ARCAVA460131"
        assert record.underlying_isin == "US78462F1030"

    def test_brazilian_underlying_isin(self, by_symbol: dict[str, CedearRecord]) -> None:
        """Brazilian ISINs start with ``BR`` and are still well-formed."""
        assert by_symbol["VALE3"].underlying_isin == "BRVALEACNOR0"

    def test_underlying_ticker_may_differ_from_the_symbol(
        self, by_symbol: dict[str, CedearRecord]
    ) -> None:
        """``SI`` is the BYMA symbol while ``SICPQ`` is the origin ticker.

        Treating the two as the same value would mislabel the underlying.
        """
        record = by_symbol["SI"]
        assert record.symbol == "SI"
        assert record.underlying_symbol == "SICPQ"

    @pytest.mark.parametrize("raw", ["", "   ", None, "ARCAVA46013", "ARCAVA4601310"])
    def test_malformed_isin_becomes_none(self, raw: str | None) -> None:
        """A malformed identifier would later be matched as if it were real."""
        assert clean_isin(raw) is None


class TestProgramStatus:
    """The page says nothing about issuance status, so nothing is assumed."""

    def test_status_stays_unknown(self, by_symbol: dict[str, CedearRecord]) -> None:
        """Presence in the list is not evidence that a program still issues.

        Assuming ``ACTIVE`` would make the deactivation rule unfalsifiable: no
        program could ever be seen as stale by this provider alone.
        """
        assert by_symbol["SPY"].program_status is ProgramStatus.UNKNOWN
        assert by_symbol["VALE3"].program_status is ProgramStatus.UNKNOWN


class TestCellCleaning:
    """Cell text arrives with markup, padding and non-breaking spaces."""

    def test_name_from_a_linked_cell(self, by_symbol: dict[str, CedearRecord]) -> None:
        """``SPY``'s name is inside an ``<a>`` element."""
        assert by_symbol["SPY"].name == "SPDR S&P 500"

    def test_name_from_a_plain_cell(self, by_symbol: dict[str, CedearRecord]) -> None:
        """``XME``'s name cell has no link."""
        assert by_symbol["XME"].name == "STATE STREET SPDR S&P METALS & MINING ETF"

    def test_nbsp_padded_amount_is_usable(self, by_symbol: dict[str, CedearRecord]) -> None:
        """``VALE3``'s amount cell is padded with ``&nbsp;`` on both sides.

        Leaving the padding in place would break numeric parsing of the
        regulatory maximum.
        """
        assert by_symbol["VALE3"].extra["max_amount_raw"] == "4.300.000"

    def test_clearing_code_is_kept(self, by_symbol: dict[str, CedearRecord]) -> None:
        """The Caja code is needed to look the program up in trade files."""
        assert by_symbol["SPY"].extra["clearing_code"] == "8549"


class TestMissingSymbols:
    """A row without a symbol cannot be stored against anything."""

    def test_row_without_a_symbol_is_rejected(self) -> None:
        """The rest of the table is still parsed."""
        page = """
        <table class="tabla-cedears">
          <tr><th>CEDEAR de ETF</th><th>Símbolo BYMA</th>
              <th>Ticker en Mercado de Origen</th><th>Mercado de Origen</th>
              <th>Ratio CEDEARs / valor subyacente</th></tr>
          <tr><td>NO TICKER</td><td>&nbsp;</td><td>X</td><td>NYSE</td><td>1:1</td></tr>
          <tr><td>SPDR S&amp;P 500</td><td>SPY</td><td>SPY</td><td>NYSE ARCA</td><td>60:1</td></tr>
        </table>
        """
        provider = CajaDeValoresCedearProvider(fetch=serve(page.encode()))
        result = provider.fetch()
        assert [r.symbol for r in result.records] == ["SPY"]
        assert any(r.reason == "missing_symbol" for r in result.rejected)
