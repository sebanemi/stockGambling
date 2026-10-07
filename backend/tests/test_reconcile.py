"""Cross-provider reconciliation tests.

COMAFI and Caja de Valores are disjoint, incomplete sources, so reconciliation
is where Phase 2's correctness actually lives. Each test class below pins one
rule from the module docstring, with the failure it prevents: a union that
shrinks the universe, a field silently taken from the wrong record, or a
disputed value resolved by precedence instead of refused.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.vocabulary import InstrumentType, ProgramStatus
from app.ingestion.reconcile import (
    MergedInstrument,
    merge_snapshots,
    ratio_is_usable,
)
from app.providers.base import (
    CedearRecord,
    CedearSnapshot,
    RejectedRecord,
)

pytestmark = pytest.mark.unit

FETCHED_AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def record(symbol: str, provider: str, **overrides: object) -> CedearRecord:
    """Build a record with sensible defaults, overridden per test."""
    fields: dict[str, object] = {
        "symbol": symbol,
        "custodian": provider,
        "name": f"{symbol} Inc",
        "instrument_type": InstrumentType.STOCK,
        "underlying_symbol": f"{symbol}-U",
        "underlying_market_raw": "NYSE",
        "ratio": Decimal(10),
        "program_status": ProgramStatus.ACTIVE,
    }
    fields.update(overrides)
    return CedearRecord(**fields)  # type: ignore[arg-type]


def snapshot(
    provider: str,
    records: Sequence[CedearRecord],
    *,
    rejected: Sequence[RejectedRecord] = (),
    warnings: Sequence[str] = (),
) -> CedearSnapshot:
    """Build a snapshot as a provider would return one."""
    return CedearSnapshot(
        provider=provider,
        source_url=f"https://example.test/{provider}",
        fetched_at=FETCHED_AT,
        records=tuple(records),
        rejected=tuple(rejected),
        warnings=tuple(warnings),
    )


class TestUnionNotIntersection:
    """Neither source is a superset, so the merge is a union."""

    def test_symbol_only_in_one_source_is_kept(self) -> None:
        """Dropping it would make the universe smaller than any source claims.

        COMAFI-only programs would vanish entirely from a merge that required
        agreement, and BYMA would show a shrinking list of tradable CEDEARs.
        """
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("AAPL", "comafi")]),
                snapshot("cajadevalores", [record("VALE3", "cajadevalores")]),
            ]
        )
        assert set(merged.by_symbol()) == {"AAPL", "VALE3"}

    def test_symbol_in_both_sources_appears_once(self) -> None:
        """A duplicate row would overwrite itself with equivalent data."""
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("AAPL", "comafi")]),
                snapshot("cajadevalores", [record("AAPL", "cajadevalores")]),
            ]
        )
        assert len(merged) == 1

    def test_no_sources_yields_an_empty_universe(self) -> None:
        """The caller, not the merge, decides whether emptiness is tolerable."""
        assert len(merge_snapshots([])) == 0


class TestFieldLevelFill:
    """Fields merge independently, so partial records still contribute."""

    def test_a_field_published_by_one_source_is_taken_from_it(self) -> None:
        """Only COMAFI states the program status, so COMAFI's value is used.

        Falling back to "whatever the first record had" would leave the status
        ``UNKNOWN`` even though an official source stated it.
        """
        merged = merge_snapshots(
            [
                snapshot(
                    "comafi", [record("AAPL", "comafi", program_status=ProgramStatus.INACTIVE)]
                ),
                snapshot(
                    "cajadevalores",
                    [record("AAPL", "cajadevalores", program_status=ProgramStatus.UNKNOWN)],
                ),
            ]
        )
        assert merged.by_symbol()["AAPL"].record.program_status is ProgramStatus.INACTIVE

    def test_fields_from_different_sources_combine(self) -> None:
        """Status from one source and ratio from the other both survive.

        Treating a record as indivisible would lose whichever field the
        higher-precedence source happened not to publish.
        """
        merged = merge_snapshots(
            [
                snapshot(
                    "comafi",
                    [record("AAPL", "comafi", program_status=ProgramStatus.INACTIVE, ratio=None)],
                ),
                snapshot(
                    "cajadevalores",
                    [record("AAPL", "cajadevalores", ratio=Decimal(20))],
                ),
            ]
        )
        result = merged.by_symbol()["AAPL"].record
        assert result.program_status is ProgramStatus.INACTIVE
        assert result.ratio == Decimal(20)

    def test_a_partial_record_does_not_erase_a_good_value(self) -> None:
        """An ``UNKNOWN`` type carries no information and must not overwrite.

        A record with unknown type winning on position would reset a
        confidently classified ETF CEDEAR to ``UNKNOWN``.
        """
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("ACWI", "comafi", instrument_type=InstrumentType.ETF)]),
                snapshot(
                    "cajadevalores",
                    [record("ACWI", "cajadevalores", instrument_type=InstrumentType.UNKNOWN)],
                ),
            ]
        )
        assert merged.by_symbol()["ACWI"].record.instrument_type is InstrumentType.ETF

    def test_a_non_conflicting_disagreement_keeps_the_first_value(self) -> None:
        """Only the ratio and the underlying are treated as conflicts.

        For a display name, either source's spelling is acceptable, so refusing
        to ingest would be a false positive.
        """
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("AAPL", "comafi", name="Apple Inc.")]),
                snapshot("cajadevalores", [record("AAPL", "cajadevalores", name="APPLE INC")]),
            ]
        )
        assert merged.by_symbol()["AAPL"].record.name == "Apple Inc."


class TestRatioConflict:
    """Two sources, two ratios: neither can be trusted."""

    def test_the_ratio_is_left_unset(self) -> None:
        """A disputed ratio must not reach the ratio history.

        Every theoretical price derived from it would be wrong by an unknown
        factor, silently and permanently.
        """
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("AAPL", "comafi", ratio=Decimal(20))]),
                snapshot("cajadevalores", [record("AAPL", "cajadevalores", ratio=Decimal(10))]),
            ]
        )
        assert merged.by_symbol()["AAPL"].record.ratio is None

    def test_the_instrument_is_still_ingested(self) -> None:
        """A real program with a disputed ratio is still a real program.

        Refusing the whole record would remove a tradable CEDEAR over a
        disagreement about one field.
        """
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("AAPL", "comafi", ratio=Decimal(20))]),
                snapshot("cajadevalores", [record("AAPL", "cajadevalores", ratio=Decimal(10))]),
            ]
        )
        assert "AAPL" in merged.by_symbol()

    def test_the_conflict_is_quarantined_with_both_values(self) -> None:
        """A human must be able to see what each source claimed."""
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("AAPL", "comafi", ratio=Decimal(20))]),
                snapshot("cajadevalores", [record("AAPL", "cajadevalores", ratio=Decimal(10))]),
            ]
        )
        conflicts = [r for r in merged.rejected if r.reason == "conflicting_ratio"]
        assert len(conflicts) == 1
        assert conflicts[0].provider == "merge"
        assert conflicts[0].symbol == "AAPL"
        assert set(conflicts[0].raw) == {"comafi", "cajadevalores"}

    def test_agreeing_ratios_are_kept(self) -> None:
        """Agreement is corroboration, not a conflict."""
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("AAPL", "comafi", ratio=Decimal(20))]),
                snapshot("cajadevalores", [record("AAPL", "cajadevalores", ratio=Decimal(20))]),
            ]
        )
        assert merged.by_symbol()["AAPL"].record.ratio == Decimal(20)
        assert not merged.rejected

    def test_a_missing_ratio_on_one_side_is_not_a_conflict(self) -> None:
        """``ASTS`` has an unparseable ratio at COMAFI and none at Caja.

        Refusing the instrument entirely over an absent value would lose a real
        program over nothing.
        """
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("ASTS", "comafi", ratio=None)]),
                snapshot("cajadevalores", [record("ASTS", "cajadevalores")]),
            ]
        )
        assert merged.by_symbol()["ASTS"].record.ratio == Decimal(10)
        assert not merged.rejected


class TestUnderlyingSymbolConflict:
    """Two sources, two underlyings: the mapping is not known."""

    def test_the_underlying_is_left_unset(self) -> None:
        """A CEDEAR pointing at the wrong instrument is worse than none.

        A wrong underlying prices against a real but incorrect series, which
        looks like a plausible number rather than an obvious failure.
        """
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("SI", "comafi", underlying_symbol="SICPQ")]),
                snapshot(
                    "cajadevalores", [record("SI", "cajadevalores", underlying_symbol="SI-U")]
                ),
            ]
        )
        assert merged.by_symbol()["SI"].record.underlying_symbol is None

    def test_the_conflict_is_quarantined(self) -> None:
        """The dispute is recorded even though the instrument is kept."""
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("SI", "comafi", underlying_symbol="SICPQ")]),
                snapshot(
                    "cajadevalores", [record("SI", "cajadevalores", underlying_symbol="SI-U")]
                ),
            ]
        )
        assert [r.reason for r in merged.rejected] == ["conflicting_underlying_symbol"]

    def test_agreeing_underlyings_are_kept(self) -> None:
        """The common case must not be flagged."""
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("AAPL", "comafi", underlying_symbol="AAPL")]),
                snapshot(
                    "cajadevalores", [record("AAPL", "cajadevalores", underlying_symbol="AAPL")]
                ),
            ]
        )
        assert merged.by_symbol()["AAPL"].record.underlying_symbol == "AAPL"


class TestNoInferenceFromSymbol:
    """Similar symbols are not the same underlying."""

    def test_a_symbol_present_in_one_source_is_not_inferred(self) -> None:
        """``BPAC11`` must not be derived from ``BPA11``.

        A CEDEAR can be re-denominated or replaced, so the resemblance is not
        evidence; inferring it would attach a foreign series to the wrong share.
        """
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("BPA11", "comafi", underlying_symbol=None)]),
                snapshot("cajadevalores", [record("BPAC11", "cajadevalores")]),
            ]
        )
        assert merged.by_symbol()["BPA11"].record.underlying_symbol is None
        assert merged.by_symbol()["BPAC11"].record.underlying_symbol == "BPAC11-U"

    def test_a_delisted_underlying_is_kept_as_published(self) -> None:
        """``SI`` wraps the delisted ``SICPQ``, and the source says so.

        Substituting a currently-listed ticker would fabricate a live mapping
        the market has not published.
        """
        merged = merge_snapshots([snapshot("cajadevalores", [record("SI", "cajadevalores")])])
        assert merged.by_symbol()["SI"].record.underlying_symbol == "SI-U"


class TestProvenance:
    """Which sources listed a symbol, and which record was stored."""

    def test_both_sources_are_recorded(self) -> None:
        """Cross-source corroboration is the reason to keep two adapters."""
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("AAPL", "comafi")]),
                snapshot("cajadevalores", [record("AAPL", "cajadevalores")]),
            ]
        )
        assert merged.by_symbol()["AAPL"].sources == ("comafi", "cajadevalores")

    def test_primary_source_is_the_first_snapshot(self) -> None:
        """Order is caller-controlled, and it decides only provenance."""
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("AAPL", "comafi")]),
                snapshot("cajadevalores", [record("AAPL", "cajadevalores")]),
            ]
        )
        item = merged.by_symbol()["AAPL"]
        assert item.primary_source == "comafi"
        assert item.record.custodian == "comafi"

    def test_source_ref_points_at_the_stored_record(self) -> None:
        """A ref to the losing record would send an operator to the wrong page."""
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("AAPL", "comafi", source_ref="https://comafi/1")]),
                snapshot(
                    "cajadevalores", [record("AAPL", "cajadevalores", source_ref="https://caja/2")]
                ),
            ]
        )
        assert merged.by_symbol()["AAPL"].record.source_ref == "https://comafi/1"

    def test_extras_are_namespaced_per_source(self) -> None:
        """``ratio_raw`` exists in both providers and must not collide.

        Overwriting one with the other would make the audit trail claim a text
        the winning source never published.
        """
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("AAPL", "comafi", extra={"ratio_raw": "20:1"})]),
                snapshot(
                    "cajadevalores",
                    [
                        record(
                            "AAPL",
                            "cajadevalores",
                            extra={"ratio_raw": "20 : 1", "max_amount_raw": "1"},
                        )
                    ],
                ),
            ]
        )
        extra = merged.by_symbol()["AAPL"].record.extra
        assert extra["comafi.ratio_raw"] == "20:1"
        assert extra["cajadevalores.ratio_raw"] == "20 : 1"
        assert extra["cajadevalores.max_amount_raw"] == "1"

    def test_single_source_symbols_list_only_that_source(self) -> None:
        """Provenance must not imply corroboration that did not happen."""
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("AAPL", "comafi")]),
                snapshot("cajadevalores", [record("VALE3", "cajadevalores")]),
            ]
        )
        assert merged.by_symbol()["AAPL"].sources == ("comafi",)


class TestPropagatedDiagnostics:
    """A provider's own rejections and warnings reach the run record."""

    def test_rejections_are_carried_through(self) -> None:
        """The audit trail must not lose the provider-level explanation."""
        rejection = RejectedRecord(
            provider="comafi", reason="malformed_product", detail="entry 3 is not an object"
        )
        merged = merge_snapshots([snapshot("comafi", [], rejected=[rejection])])
        assert merged.rejected == (rejection,)

    def test_warnings_are_prefixed_with_the_provider(self) -> None:
        """An unprefixed warning cannot be acted on without knowing its source."""
        merged = merge_snapshots(
            [
                snapshot("comafi", [], warnings=["ASTS: ratio '15.1' is not unambiguous"]),
                snapshot("cajadevalores", [], warnings=["XYZ: unknown market"]),
            ]
        )
        assert merged.warnings == (
            "comafi: ASTS: ratio '15.1' is not unambiguous",
            "cajadevalores: XYZ: unknown market",
        )

    def test_quarantine_entries_and_provider_rejections_coexist(self) -> None:
        """Both kinds of problem belong in the same place."""
        rejection = RejectedRecord(provider="comafi", reason="malformed_product", detail="bad")
        merged = merge_snapshots(
            [
                snapshot(
                    "comafi", [record("AAPL", "comafi", ratio=Decimal(20))], rejected=[rejection]
                ),
                snapshot("cajadevalores", [record("AAPL", "cajadevalores", ratio=Decimal(10))]),
            ]
        )
        assert {r.reason for r in merged.rejected} == {"malformed_product", "conflicting_ratio"}


class TestOrdering:
    """Symbol order follows first appearance, not sorting."""

    def test_order_follows_first_appearance(self) -> None:
        """A stable order keeps successive runs diffable."""
        merged = merge_snapshots(
            [
                snapshot("comafi", [record("ZZZ", "comafi"), record("AAA", "comafi")]),
                snapshot("cajadevalores", [record("MMM", "cajadevalores")]),
            ]
        )
        assert [item.record.symbol for item in merged.instruments] == ["ZZZ", "AAA", "MMM"]


class TestRatioIsUsable:
    """The gate on writing a ratio period."""

    def test_a_positive_ratio_is_usable(self) -> None:
        """The normal case opens a period."""
        assert ratio_is_usable(record("AAPL", "comafi")) is True

    def test_a_missing_ratio_is_not(self) -> None:
        """No ratio means no period, and downstream pricing must refuse."""
        assert ratio_is_usable(record("ASTS", "comafi", ratio=None)) is False

    def test_a_zero_ratio_is_not(self) -> None:
        """Zero would collapse the theoretical price to nothing."""
        assert ratio_is_usable(record("X", "comafi", ratio=Decimal(0))) is False


def test_merged_instrument_is_immutable() -> None:
    """Snapshots are compared across runs, so a merge must not be mutable."""
    merged = merge_snapshots([snapshot("comafi", [record("AAPL", "comafi")])])
    item: MergedInstrument = merged.instruments[0]
    with pytest.raises(AttributeError):
        item.primary_source = "other"  # type: ignore[misc]
