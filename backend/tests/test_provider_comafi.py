"""COMAFI provider tests, driven by a recorded payload.

The fixture is a verbatim capture of the live endpoint, including every defect
documented in the provider module: the duplicated ``WDC`` symbol, the ambiguous
``ASTS`` ratio, the sector-instead-of-venue rows, and the missing-venue
placeholder. Tests assert on the feed as published, not on an idealised feed.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.domain.vocabulary import InstrumentType, ProgramStatus, UnderlyingMarket
from app.providers.base import CedearRecord, CedearSnapshot, ProviderError
from app.providers.http import FetchCallable
from app.providers.implementations.comafi import PRODUCTS_URL, ComafiCedearProvider
from app.providers.implementations.comafi.provider import clean_isin, parse_description

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).parent / "fixtures" / "comafi" / "getproducts.json"


def load_payload() -> dict[str, Any]:
    """Return the recorded endpoint payload."""
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload


def serve(body: bytes) -> FetchCallable:
    """Build a fetcher that returns ``body`` regardless of the requested URL."""
    return lambda _url: body


@pytest.fixture
def snapshot() -> CedearSnapshot:
    """Fetch the recorded payload through a fake transport."""
    return ComafiCedearProvider(fetch=serve(FIXTURE.read_bytes())).fetch()


@pytest.fixture
def by_symbol(snapshot: CedearSnapshot) -> dict[str, CedearRecord]:
    """Index the snapshot's records by symbol for readable assertions."""
    return {record.symbol: record for record in snapshot.records}


class TestSnapshotShape:
    """The provider reports provenance alongside the data."""

    def test_provider_name_and_url(self, snapshot: CedearSnapshot) -> None:
        """Every record must be traceable to where it came from."""
        assert snapshot.provider == "comafi"
        assert snapshot.source_url == PRODUCTS_URL

    def test_fetched_at_is_timezone_aware(self, snapshot: CedearSnapshot) -> None:
        """A naive timestamp would be ambiguous across Argentina and UTC."""
        assert snapshot.fetched_at.utcoffset() is not None


class TestSymbolHandling:
    """Symbols carry a trailing newline in the live feed."""

    def test_trailing_newline_is_stripped(self, by_symbol: dict[str, CedearRecord]) -> None:
        r"""``"WDC\n"`` and ``"WDC"`` are one instrument, not two."""
        assert "WDC\n" not in by_symbol
        assert "WDC" in by_symbol

    def test_symbol_with_a_space_survives(self, by_symbol: dict[str, CedearRecord]) -> None:
        """``IWDA LN`` is a single ticker; splitting it invents an instrument."""
        assert "IWDA LN" in by_symbol


class TestDuplicateResolution:
    """``WDC`` is published twice: once as a stub, once complete."""

    def test_only_one_record_survives(self, by_symbol: dict[str, CedearRecord]) -> None:
        """One symbol must yield one record or the upsert becomes order-dependent."""
        assert sum(1 for symbol in by_symbol if symbol == "WDC") == 1

    def test_the_ratio_bearing_record_wins(self, by_symbol: dict[str, CedearRecord]) -> None:
        """Keep the record carrying a ratio.

        The stub has no ratio and the wrong underlying ticker, so keeping it
        would overwrite a real conversion ratio with ``None`` on every run.
        """
        assert by_symbol["WDC"].ratio == Decimal(92)

    def test_the_loser_is_rejected_with_its_reference(self, snapshot: CedearSnapshot) -> None:
        """A dropped record is retained for audit, including its detail link."""
        supersessions = [
            r for r in snapshot.rejected if r.reason == "superseded_by_ratio_bearing_record"
        ]
        assert len(supersessions) == 1
        assert "WDC" in supersessions[0].detail


class TestRatioParsing:
    """Ratios are ``N:D`` fractions, and the feed publishes one malformed one."""

    def test_integral_ratio(self, by_symbol: dict[str, CedearRecord]) -> None:
        """``92:1`` means 92 CEDEARs per one underlying share."""
        assert by_symbol["WDC"].ratio == Decimal(92)

    def test_fractional_ratio(self, by_symbol: dict[str, CedearRecord]) -> None:
        """``1:4`` is 0.25, not 1."""
        assert by_symbol["NG"].ratio == Decimal("0.25")

    def test_ambiguous_ratio_is_left_unset_and_warned(
        self, snapshot: CedearSnapshot, by_symbol: dict[str, CedearRecord]
    ) -> None:
        """``15.1`` cannot be resolved, so the instrument is kept without a ratio.

        Dropping the record entirely would lose a real program; keeping a
        guessed ratio would corrupt every theoretical price computed from it.
        """
        assert by_symbol["ASTS"].ratio is None
        assert any("ASTS" in warning and "15.1" in warning for warning in snapshot.warnings)

    def test_original_text_is_retained(self, by_symbol: dict[str, CedearRecord]) -> None:
        """The raw value is kept so the refusal can be reviewed later."""
        assert by_symbol["ASTS"].extra["ratio_raw"] == "15.1"


class TestUnderlyingMarket:
    """The venue field is unreliable and is never guessed."""

    def test_known_venue(self, by_symbol: dict[str, CedearRecord]) -> None:
        """``NYSEAmerican`` is a venue we map."""
        assert by_symbol["NG"].underlying_market is UnderlyingMarket.NYSE_AMERICAN

    def test_sector_in_the_venue_field_stays_unknown(
        self, snapshot: CedearSnapshot, by_symbol: dict[str, CedearRecord]
    ) -> None:
        """``"Idustrial Gases"`` is LIN's sector, not its exchange.

        Recording a venue here would make the system look up a price series on
        the wrong market, silently. ``UNKNOWN`` forces a human decision.
        """
        assert by_symbol["LIN"].underlying_market is UnderlyingMarket.UNKNOWN
        assert any("Idustrial Gases" in w for w in snapshot.warnings)

    def test_country_level_label_stays_unknown(self, by_symbol: dict[str, CedearRecord]) -> None:
        """``"New York"`` names several venues, so it maps to none of them."""
        assert by_symbol["ECL"].underlying_market is UnderlyingMarket.UNKNOWN

    def test_missing_venue_is_not_a_warning(self, snapshot: CedearSnapshot) -> None:
        """``"none"`` means the source said nothing, which needs no human.

        Warning on it would bury the real problems in noise and train
        operators to ignore warnings.
        """
        assert not any("AABA" in warning for warning in snapshot.warnings)

    def test_raw_value_is_always_retained(self, by_symbol: dict[str, CedearRecord]) -> None:
        """The unmapped text is kept so a mapping can be added later."""
        assert by_symbol["LIN"].underlying_market_raw == "Idustrial Gases"


class TestProgramStatus:
    """Status comes from a free-text sentence, not a code."""

    def test_active(self, by_symbol: dict[str, CedearRecord]) -> None:
        """The enabled wording maps to ``ACTIVE``."""
        assert by_symbol["AAPL"].program_status is ProgramStatus.ACTIVE

    def test_inactive(self, by_symbol: dict[str, CedearRecord]) -> None:
        """A disabled program is retained as ``INACTIVE``, never deleted."""
        assert by_symbol["AABA"].program_status is ProgramStatus.INACTIVE

    def test_raw_text_is_retained(self, by_symbol: dict[str, CedearRecord]) -> None:
        """The interpretation is auditable against the published sentence."""
        assert by_symbol["AABA"].program_status_raw is not None


class TestIsinValidation:
    """Identifiers are validated rather than stored on trust."""

    def test_well_formed_isin_is_kept(self, by_symbol: dict[str, CedearRecord]) -> None:
        """Valid identifiers are the primary cross-source matching key."""
        assert by_symbol["WDC"].isin == "AR0307648912"
        assert by_symbol["WDC"].underlying_isin == "US9581021055"

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", None, "AR030764891", "0307648912", "AR-0307648912", "A1R0307648912"],
    )
    def test_malformed_isin_becomes_none(self, raw: str | None) -> None:
        """A malformed identifier would later be matched as if it were real.

        The discarded ``WDC`` stub publishes an empty ``tip``; a value of the
        wrong length must fail the same way rather than being stored.
        """
        assert clean_isin(raw) is None

    def test_lowercase_and_spaces_are_normalised(self) -> None:
        """Formatting differences in the feed are not different identifiers."""
        assert clean_isin(" ar 0307648912 ") == "AR0307648912"


class TestInstrumentType:
    """The ``section`` field distinguishes share from ETF programs."""

    def test_share_program(self, by_symbol: dict[str, CedearRecord]) -> None:
        """``Cedear Shares`` maps to ``STOCK``."""
        assert by_symbol["AAPL"].instrument_type is InstrumentType.STOCK

    def test_etf_program(self, by_symbol: dict[str, CedearRecord]) -> None:
        """``Cedear ETF`` maps to ``ETF``, not ``STOCK``.

        ETF CEDEARs are priced on the underlying's NAV-adjusted series, so the
        type drives a different valuation path downstream.
        """
        assert by_symbol["ACWI"].instrument_type is InstrumentType.ETF


class TestParseDescription:
    """Label spelling varies inside a single feed."""

    def test_colon_inside_strong(self) -> None:
        """``<strong>Label:</strong>Value`` is parsed."""
        html = "<li><strong>Nombre:</strong> Apple Inc</li>"
        assert parse_description(html)["NOMBRE"] == "Apple Inc"

    def test_colon_outside_strong(self) -> None:
        """``<strong>Label</strong>: Value`` is parsed identically."""
        html = "<li><strong>Nombre</strong>: Apple Inc</li>"
        assert parse_description(html)["NOMBRE"] == "Apple Inc"

    def test_accents_and_newlines_in_the_key(self) -> None:
        """``Símbolo`` and ``Simbolo BYMA`` resolve to the same key."""
        html = "<li><strong>Símbolo\nBYMA</strong>: AAPL</li>"
        assert parse_description(html)["SIMBOLOBYMA"] == "AAPL"

    def test_item_without_a_label_is_skipped(self) -> None:
        """Unlabelled list items are decoration, not data."""
        assert parse_description("<li>plain text</li>") == {}

    def test_first_occurrence_wins(self) -> None:
        """A repeated label must not overwrite the value already captured."""
        html = "<li><strong>Nombre</strong>: First</li><li><strong>Nombre</strong>: Second</li>"
        assert parse_description(html)["NOMBRE"] == "First"


class TestFailureModes:
    """A changed endpoint is an error, not a silent empty universe."""

    def test_missing_products_key(self) -> None:
        """A shape change must abort the snapshot and the whole run."""
        provider = ComafiCedearProvider(fetch=serve(json.dumps({"data": []}).encode()))
        with pytest.raises(ProviderError, match="products"):
            provider.fetch()

    def test_payload_is_not_an_object(self) -> None:
        """A JSON array or bare value is also a shape change."""
        provider = ComafiCedearProvider(fetch=serve(b"[]"))
        with pytest.raises(ProviderError, match="products"):
            provider.fetch()

    def test_invalid_json(self) -> None:
        """An HTML error page is not a parseable payload.

        It must surface as :class:`ProviderError`, not as a raw
        ``JSONDecodeError``: the ingestion service only recognises the former
        as a provider failure, and an unrecognised exception type would skip the
        deactivation guard.
        """
        provider = ComafiCedearProvider(fetch=serve(b"<html>503</html>"))
        with pytest.raises(ProviderError, match="did not return JSON"):
            provider.fetch()

    def test_non_object_entry_is_rejected_individually(self) -> None:
        """One bad entry must not discard the rest of the universe."""
        payload = load_payload()
        payload["products"].insert(0, "not-an-object")
        provider = ComafiCedearProvider(fetch=serve(json.dumps(payload).encode()))
        result = provider.fetch()
        assert len(result.records) == 14
        assert any(r.reason == "malformed_product" for r in result.rejected)

    def test_unnamed_entry_is_rejected(self) -> None:
        """Without a symbol there is nothing to store against."""
        payload = load_payload()
        payload["products"].append({"description": "no name here"})
        provider = ComafiCedearProvider(fetch=serve(json.dumps(payload).encode()))
        result = provider.fetch()
        assert any(r.reason == "missing_symbol" for r in result.rejected)
