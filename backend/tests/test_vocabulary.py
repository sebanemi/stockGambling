"""Domain vocabulary tests.

These cover the translation layer between the official feeds and the platform's
own vocabulary. The feeds are messy in specific, documented ways, so each test
below pins one of those ways rather than testing the happy path in general.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.vocabulary import (
    InstrumentType,
    ProgramStatus,
    UnderlyingMarket,
    collapse_whitespace,
    format_ratio,
    is_known_market,
    normalise_market,
    normalise_program_status,
    normalise_symbol,
    parse_ratio,
    strip_accents,
)

pytestmark = pytest.mark.unit


class TestCollapseWhitespace:
    """The feeds embed newlines and non-breaking spaces inside values."""

    def test_newline_and_nbsp_are_normalised(self) -> None:
        """A trailing newline and a non-breaking space collapse to one space."""
        assert collapse_whitespace("20:1\n") == "20:1"
        assert collapse_whitespace("\xa0 14.700.000\xa0") == "14.700.000"

    def test_internal_runs_collapse_to_one_space(self) -> None:
        """Cell text with line breaks must not become a run of spaces."""
        assert collapse_whitespace("BYMA\n\n   ticker") == "BYMA ticker"

    def test_none_and_empty_both_yield_empty_string(self) -> None:
        """Callers treat falsy and whitespace-only values the same way."""
        assert collapse_whitespace(None) == ""
        assert collapse_whitespace("   ") == ""


class TestStripAccents:
    """Header lookup must work whether or not the source escaped the accent."""

    def test_accented_and_unaccented_forms_match(self) -> None:
        """``Símbolo`` and ``Simbolo`` reduce to the same characters."""
        assert strip_accents("Símbolo BYMA") == strip_accents("Simbolo BYMA")

    def test_tilde_is_removed(self) -> None:
        """``Codigo`` and ``Código`` differ only by the tilde."""
        assert strip_accents("Código") == "Codigo"


class TestParseRatio:
    """The ratio is a fraction, published as ``N:D``, and is never guessed."""

    def test_cedears_per_underlying_unit(self) -> None:
        """``60:1`` means sixty CEDEARs represent one ETF share."""
        assert parse_ratio("60:1") == Decimal(60)

    def test_fractional_orientation_is_handled(self) -> None:
        """``1:5`` means one CEDEAR represents five shares, i.e. 0.2.

        Storing this as the integer ``1`` would be off by a factor of five for
        every theoretical price of the program.
        """
        assert parse_ratio("1:5") == Decimal("0.2")

    def test_stray_whitespace_around_the_colon(self) -> None:
        """The source sometimes emits ``"1 :4"``."""
        assert parse_ratio("1 :4") == Decimal("0.25")

    def test_trailing_newline_is_tolerated(self) -> None:
        """COMAFI publishes ratios with a trailing newline."""
        assert parse_ratio("3:1\n") == Decimal(3)

    def test_ambiguous_value_is_refused(self) -> None:
        """``15.1`` could be ``15:1`` or ``1.5:1`` and must not be resolved.

        Guessing here would corrupt every future theoretical price, so the
        value is refused and the instrument is stored without a ratio.
        """
        assert parse_ratio("15.1") is None

    def test_missing_and_blank_values(self) -> None:
        """Absent values are ``None``, not zero and not one."""
        assert parse_ratio(None) is None
        assert parse_ratio("") is None
        assert parse_ratio("   ") is None

    def test_zero_denominator_is_refused(self) -> None:
        """``1:0`` is not a ratio; returning infinity would poison the engine."""
        assert parse_ratio("1:0") is None

    def test_non_numeric_is_refused(self) -> None:
        """A free-text value is not a number."""
        assert parse_ratio("sin datos") is None

    def test_result_is_quantised_to_the_column_scale(self) -> None:
        """A re-ingest must not see a spurious difference on every row.

        ``1:3`` is not exact in binary floating point, so the result is
        quantised to the same scale as the ``NUMERIC(20, 10)`` column.
        """
        assert str(parse_ratio("1:3")) == "0.3333333333"
        assert str(parse_ratio("1:7")) == "0.1428571429"


class TestFormatRatio:
    """The API renders the published ``N:D`` form, not the stored fraction."""

    def test_integral_ratio_renders_as_n_to_one(self) -> None:
        """``60`` renders as ``60:1``."""
        assert format_ratio(Decimal(60)) == "60:1"

    def test_fractional_ratio_renders_as_one_to_n(self) -> None:
        """``0.2`` renders as ``1:5``, the way the source publishes it."""
        assert format_ratio(Decimal("0.2")) == "1:5"

    def test_round_trip_through_parse(self) -> None:
        """Parsing the rendered form returns the original value."""
        for raw in ("60:1", "1:5", "1:4", "20:1"):
            ratio = parse_ratio(raw)
            assert ratio is not None
            assert parse_ratio(format_ratio(ratio)) == ratio

    def test_none_stays_none(self) -> None:
        """An unknown ratio must not become ``"None:1"``."""
        assert format_ratio(None) is None


class TestNormaliseMarket:
    """Vendor spellings map onto canonical venue codes, or onto ``UNKNOWN``."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("NYSE", UnderlyingMarket.NYSE),
            ("New York Stock Exchange", UnderlyingMarket.NYSE),
            ("nyseamerican", UnderlyingMarket.NYSE_AMERICAN),
            ("NYSE American", UnderlyingMarket.NYSE_AMERICAN),
            ("NYSE ARCA", UnderlyingMarket.NYSE_ARCA),
            ("NASDAQ GM", UnderlyingMarket.NASDAQ_GM),
            ("Nasdaq Capital Market", UnderlyingMarket.NASDAQ_CM),
            ("B3", UnderlyingMarket.B3),
            ("Xetra", UnderlyingMarket.XETRA),
        ],
    )
    def test_known_spellings(self, raw: str, expected: UnderlyingMarket) -> None:
        """Each published spelling resolves to its canonical code."""
        assert normalise_market(raw) is expected

    def test_unambiguous_case_insensitive(self) -> None:
        """Case and surrounding whitespace are not information."""
        assert normalise_market("  nyse  ") is UnderlyingMarket.NYSE

    def test_ambiguous_country_label_is_not_guessed(self) -> None:
        """The label "New York" denotes several venues and is unmapped.

        Conflating it with NYSE would fabricate a fact that changes how the
        underlying price series is retrieved.
        """
        assert normalise_market("New York") is UnderlyingMarket.UNKNOWN
        assert normalise_market("USA") is UnderlyingMarket.UNKNOWN

    def test_sector_in_the_market_field_is_not_guessed(self) -> None:
        """COMAFI sometimes publishes a sector where a venue belongs."""
        assert normalise_market("Idustrial Gases") is UnderlyingMarket.UNKNOWN

    def test_missing_values_are_unknown_not_errors(self) -> None:
        """A blank venue is expected, not a failure."""
        for raw in (None, "", "   ", "N/A", "-", "null"):
            assert normalise_market(raw) is UnderlyingMarket.UNKNOWN

    def test_is_known_market(self) -> None:
        """The convenience predicate agrees with the mapping."""
        assert is_known_market("NYSE") is True
        assert is_known_market("Idustrial Gases") is False


class TestNormaliseProgramStatus:
    """The status field is a sentence, and only its issuance wording is read."""

    def test_active_wording(self) -> None:
        """The wording "Habilitado para emitir y cancelar" means the program is usable."""
        assert normalise_program_status("Habilitado para emitir y cancelar") is ProgramStatus.ACTIVE

    def test_inactive_wording(self) -> None:
        """Issuance-disabled programs stay listed on BYMA and are not deleted."""
        assert (
            normalise_program_status("Inhabilitado para emitir y cancelar")
            is ProgramStatus.INACTIVE
        )

    def test_misspelling_does_not_flip_the_classification(self) -> None:
        """COMAFI publishes "Inhablitado" in a few records.

        ``INACTIVE`` is matched first precisely because it contains
        ``habilitado``; matching the active marker alone would classify a
        disabled program as active.
        """
        assert normalise_program_status("Inhablitado para emitir") is ProgramStatus.INACTIVE

    def test_accented_and_nbsp_heavy_text(self) -> None:
        """Diacritics and non-breaking spaces do not hide the marker."""
        assert normalise_program_status("\xa0Habilitado\xa0 para emitir") is ProgramStatus.ACTIVE

    def test_unrelated_text_is_unknown(self) -> None:
        """Silence is not evidence of either state."""
        assert normalise_program_status("Sin observaciones") is ProgramStatus.UNKNOWN
        assert normalise_program_status(None) is ProgramStatus.UNKNOWN

    def test_inactive_wins_over_active_substring(self) -> None:
        """A sentence containing both markers classifies as inactive."""
        assert (
            normalise_program_status("Programa habilitado y inhabilitado") is ProgramStatus.INACTIVE
        )


class TestNormaliseSymbol:
    """Tickers arrive with trailing newlines and inconsistent case."""

    def test_trailing_newline_and_case(self) -> None:
        r"""COMAFI publishes ``"WDC\n"``."""
        assert normalise_symbol("WDC\n") == "WDC"
        assert normalise_symbol("aapl") == "AAPL"

    def test_internal_spaces_are_meaningful(self) -> None:
        """``"IWDA LN"`` is one ticker; splitting it would invent two."""
        assert normalise_symbol(" iwda ln ") == "IWDA LN"

    def test_empty_becomes_none(self) -> None:
        """A missing symbol is absent, not an empty-string lookup key."""
        assert normalise_symbol("") is None
        assert normalise_symbol(None) is None
        assert normalise_symbol("\xa0") is None


class TestInstrumentType:
    """The type distinguishes a share CEDEAR from an ETF CEDEAR."""

    def test_values_are_stable_strings(self) -> None:
        """They are persisted, so the wire values must not drift."""
        assert InstrumentType.STOCK.value == "STOCK"
        assert InstrumentType.ETF.value == "ETF"
        assert InstrumentType.OTHER.value == "OTHER"
        assert InstrumentType.UNKNOWN.value == "UNKNOWN"
