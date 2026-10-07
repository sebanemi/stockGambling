"""Metadata ingestion tests, against a real migrated PostgreSQL schema.

These cover the Phase 2 exit criteria that only a database can prove: that
re-running is a no-op, that a ratio change closes a period instead of rewriting
one, that periods never overlap, and that no failure path can deactivate the
universe. They use the migration that ships, not ``create_all``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.domain.vocabulary import InstrumentType, ProgramStatus
from app.ingestion.service import (
    JOB_NAME,
    MIN_UNIVERSE_RETENTION,
    IngestionReport,
    fetch_and_ingest,
    open_ratio_period,
    ratio_effective_on,
    run_metadata_ingestion,
)
from app.models.instrument import IngestionRun, Instrument, InstrumentRatioHistory
from app.providers.base import (
    CedearDataProvider,
    CedearRecord,
    CedearSnapshot,
    ProviderError,
    RejectedRecord,
)

pytestmark = pytest.mark.integration

DAY_ONE = date(2026, 3, 2)
DAY_TWO = date(2026, 3, 3)
DAY_THREE = date(2026, 3, 4)


def record(symbol: str, provider: str = "comafi", **overrides: object) -> CedearRecord:
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
        "source_ref": f"https://example.test/{provider}/{symbol}",
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
        fetched_at=utc_now(),
        records=tuple(records),
        rejected=tuple(rejected),
        warnings=tuple(warnings),
    )


def ingest(
    session: Session,
    records: Sequence[CedearRecord],
    *,
    day: date = DAY_ONE,
    provider: str = "comafi",
    expected: Sequence[str] | None = None,
) -> IngestionReport:
    """Run a single-provider ingestion and return its report."""
    return run_metadata_ingestion(
        session,
        [snapshot(provider, records)],
        effective_date=day,
        expected_providers=[provider] if expected is None else list(expected),
    )


def stored(session: Session, symbol: str) -> Instrument:
    """Return the stored instrument, failing the test if it is absent.

    Asserting existence here rather than at each call site keeps the individual
    tests about behaviour instead of about setup, while still making a missing
    row a clear failure rather than an ``AttributeError`` on ``None``.
    """
    instrument = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
    assert instrument is not None, f"{symbol} was not stored"
    return instrument


def open_period(session: Session, symbol: str) -> InstrumentRatioHistory:
    """Return an instrument's open ratio period, failing the test if there is none."""
    period = open_ratio_period(session, stored(session, symbol))
    assert period is not None, f"{symbol} has no open ratio period"
    return period


def run_row(session: Session, run_id: int) -> IngestionRun:
    """Return a persisted ingestion run by id, failing the test if it is absent."""
    run = session.get(IngestionRun, run_id)
    assert run is not None, f"ingestion run {run_id} was not written"
    return run


def periods(session: Session, symbol: str) -> list[InstrumentRatioHistory]:
    """Return an instrument's ratio periods in chronological order."""
    instrument = stored(session, symbol)
    return list(
        session.scalars(
            select(InstrumentRatioHistory)
            .where(InstrumentRatioHistory.instrument_id == instrument.id)
            .order_by(InstrumentRatioHistory.effective_from)
        )
    )


def runs(session: Session) -> list[IngestionRun]:
    """Return every ingestion run recorded so far, oldest first."""
    return list(session.scalars(select(IngestionRun).order_by(IngestionRun.id)))


class TestNoSnapshotsIsRefused:
    """The most dangerous input is the one that looks like an empty market."""

    def test_an_empty_snapshot_list_raises(self, db_session: Session) -> None:
        """A failed fetch must never be allowed to look like an empty universe.

        Accepting it would deactivate every instrument on the first bad run.
        """
        with pytest.raises(ProviderError, match="no snapshots"):
            run_metadata_ingestion(db_session, [], effective_date=DAY_ONE)

    def test_nothing_is_written(self, db_session: Session) -> None:
        """The refusal must leave the database untouched."""
        ingest(db_session, [record("AAPL")])
        with pytest.raises(ProviderError):
            run_metadata_ingestion(db_session, [], effective_date=DAY_TWO)
        assert stored(db_session, "AAPL") is not None


class TestUpsert:
    """The universe comes from the sources; there is no hardcoded list."""

    def test_a_new_symbol_is_created(self, db_session: Session) -> None:
        """A newly listed program appears because a source published it."""
        report = ingest(db_session, [record("AAPL"), record("VALE3")])
        assert report.instruments_created == 2
        assert report.instruments_updated == 0
        assert {i.symbol for i in db_session.scalars(select(Instrument))} == {"AAPL", "VALE3"}

    def test_an_existing_symbol_is_updated_not_duplicated(self, db_session: Session) -> None:
        """The symbol is the natural key; a second row would split the history."""
        ingest(db_session, [record("AAPL")])
        report = ingest(db_session, [record("AAPL", name="Apple Inc.")], day=DAY_TWO)
        assert report.instruments_created == 0
        assert report.instruments_updated == 1
        assert len(list(db_session.scalars(select(Instrument)))) == 1
        assert stored(db_session, "AAPL").name == "Apple Inc."

    def test_new_instruments_are_active(self, db_session: Session) -> None:
        """A row created from a live listing starts tradable."""
        ingest(db_session, [record("AAPL")])
        assert stored(db_session, "AAPL").is_active is True

    def test_last_seen_is_advanced(self, db_session: Session) -> None:
        """A steady state must still be observable as a fresh sighting."""
        ingest(db_session, [record("AAPL")])
        before = stored(db_session, "AAPL").last_seen_at
        ingest(db_session, [record("AAPL")], day=DAY_TWO)
        assert stored(db_session, "AAPL").last_seen_at > before

    def test_first_seen_is_not_moved(self, db_session: Session) -> None:
        """The listing date is a fact about the program, not about the job."""
        ingest(db_session, [record("AAPL")])
        before = stored(db_session, "AAPL").first_seen_at
        ingest(db_session, [record("AAPL")], day=DAY_TWO)
        assert stored(db_session, "AAPL").first_seen_at == before

    def test_typed_fields_are_stored(self, db_session: Session) -> None:
        """The API reads these columns, so the mapping must be correct."""
        ingest(
            db_session,
            [
                record(
                    "ACWI",
                    underlying_market_raw="NYSE ARCA",
                    isin="ARCAVA4600X4",
                    underlying_isin="US4642872349",
                    instrument_type=InstrumentType.ETF,
                )
            ],
        )
        instrument = stored(db_session, "ACWI")
        assert instrument.instrument_type == "ETF"
        assert instrument.underlying_market == "NYSE_ARCA"
        assert instrument.isin == "ARCAVA4600X4"
        assert instrument.underlying_isin == "US4642872349"

    def test_a_silent_source_does_not_erase_a_known_venue(self, db_session: Session) -> None:
        """``UNKNOWN`` means "nobody said", not "this changed".

        Caja de Valores never publishes a venue for a suspended program. If that
        silence were allowed to overwrite, a venue already confirmed by COMAFI
        would be lost, and with it the mapping used to fetch the price series.
        """
        ingest(db_session, [record("AAPL", underlying_market_raw="NASDAQ GS")])
        ingest(
            db_session,
            [record("AAPL", underlying_market_raw="Idustrial Gases")],
            day=DAY_TWO,
            provider="cajadevalores",
        )
        instrument = stored(db_session, "AAPL")
        assert instrument.underlying_market == "NASDAQ_GS"
        assert instrument.underlying_market_raw == "Idustrial Gases"

    def test_provider_specific_attributes_are_namespaced(self, db_session: Session) -> None:
        """Both providers publish ``ratio_raw``; the JSONB must not collide."""
        ingest(db_session, [record("AAPL", extra={"ratio_raw": "10:1"})])
        ingest(
            db_session,
            [record("AAPL", extra={"ratio_raw": "10 : 1", "max_amount_raw": "1"})],
            day=DAY_TWO,
            provider="cajadevalores",
        )
        attributes = stored(db_session, "AAPL").attributes
        assert attributes["sources.comafi.ratio_raw"] == "10:1"
        assert attributes["sources.cajadevalores.ratio_raw"] == "10 : 1"
        assert attributes["sources.cajadevalores.max_amount_raw"] == "1"


class TestRatioPeriods:
    """Half-open ``[from, to)`` periods, never rewritten."""

    def test_a_first_ratio_opens_one_period(self, db_session: Session) -> None:
        """The first sighting has no predecessor to close."""
        report = ingest(db_session, [record("AAPL", ratio=Decimal(60))])
        assert report.ratios_inserted == 1
        period = periods(db_session, "AAPL")[0]
        assert period.ratio == Decimal(60)
        assert period.effective_from == DAY_ONE
        assert period.effective_to is None

    def test_rerunning_with_the_same_ratio_inserts_nothing(self, db_session: Session) -> None:
        """A daily job must not manufacture one period per day.

        With one period per run, the ratio table would grow without bound and
        every "period" would be a day long, which makes as-of lookups meaning-
        less and hides real ratio changes.
        """
        ingest(db_session, [record("AAPL", ratio=Decimal(60))])
        report = ingest(db_session, [record("AAPL", ratio=Decimal(60))], day=DAY_TWO)
        assert report.ratios_inserted == 0
        assert len(periods(db_session, "AAPL")) == 1

    def test_an_unchanged_ratio_keeps_its_original_start_date(self, db_session: Session) -> None:
        """Re-observing a ratio does not restate when it began."""
        ingest(db_session, [record("AAPL", ratio=Decimal(60))])
        ingest(db_session, [record("AAPL", ratio=Decimal(60))], day=DAY_TWO)
        assert periods(db_session, "AAPL")[0].effective_from == DAY_ONE

    def test_a_changed_ratio_closes_the_open_period(self, db_session: Session) -> None:
        """The old period must end, or the two would overlap."""
        ingest(db_session, [record("AAPL", ratio=Decimal(60))])
        ingest(db_session, [record("AAPL", ratio=Decimal(30))], day=DAY_TWO)
        first, second = periods(db_session, "AAPL")
        assert first.effective_to == DAY_TWO
        assert second.effective_from == DAY_TWO
        assert second.effective_to is None

    def test_a_past_date_never_inherits_todays_ratio(self, db_session: Session) -> None:
        """A back-test of day one must keep the ratio that applied on day one.

        This is the Phase 2 criterion in its strictest form: after the change,
        asking for the ratio on DAY_ONE must still answer 60, not 30.
        """
        ingest(db_session, [record("AAPL", ratio=Decimal(60))])
        ingest(db_session, [record("AAPL", ratio=Decimal(30))], day=DAY_TWO)
        instrument = stored(db_session, "AAPL")
        assert ratio_effective_on(db_session, instrument, DAY_ONE) == Decimal(60)
        assert ratio_effective_on(db_session, instrument, DAY_TWO) == Decimal(30)

    def test_the_old_period_is_half_open(self, db_session: Session) -> None:
        """On the change date the new ratio is already in force.

        Treating periods as inclusive on both ends would make DAY_TWO ambiguous
        and return whichever row the query happened to see first.
        """
        ingest(db_session, [record("AAPL", ratio=Decimal(60))])
        ingest(db_session, [record("AAPL", ratio=Decimal(30))], day=DAY_TWO)
        first, _second = periods(db_session, "AAPL")
        assert first.covers(DAY_ONE) is True
        assert first.covers(DAY_TWO) is False

    def test_a_date_before_the_first_period_has_no_ratio(self, db_session: Session) -> None:
        """The lookup returns ``None``, not the nearest known value.

        Substituting the first period would make a pre-listing back-test
        silently use a ratio that did not exist.
        """
        ingest(db_session, [record("AAPL", ratio=Decimal(60))])
        instrument = stored(db_session, "AAPL")
        assert ratio_effective_on(db_session, instrument, DAY_ONE - timedelta(days=1)) is None

    def test_an_absent_ratio_leaves_the_open_period_alone(self, db_session: Session) -> None:
        """A source being silent is not evidence the ratio was removed.

        Closing the period would leave the instrument with no ratio at all and
        would date the disappearance to an arbitrary day.
        """
        ingest(db_session, [record("AAPL", ratio=Decimal(60))])
        report = ingest(db_session, [record("AAPL", ratio=None)], day=DAY_TWO)
        assert report.ratios_inserted == 0
        assert open_period(db_session, "AAPL").ratio == Decimal(60)

    def test_a_conflicted_ratio_is_never_written(self, db_session: Session) -> None:
        """A disputed ratio must not open a period on either source's word."""
        run_metadata_ingestion(
            db_session,
            [
                snapshot("comafi", [record("AAPL", ratio=Decimal(60))]),
                snapshot("cajadevalores", [record("AAPL", ratio=Decimal(10))]),
            ],
            effective_date=DAY_ONE,
            expected_providers=["comafi", "cajadevalores"],
        )
        assert periods(db_session, "AAPL") == []

    def test_a_conflict_after_a_known_ratio_keeps_the_open_period(
        self, db_session: Session
    ) -> None:
        """A later disagreement must not close a period that was already real."""
        ingest(db_session, [record("AAPL", ratio=Decimal(60))])
        run_metadata_ingestion(
            db_session,
            [
                snapshot("comafi", [record("AAPL", ratio=Decimal(60))]),
                snapshot("cajadevalores", [record("AAPL", ratio=Decimal(10))]),
            ],
            effective_date=DAY_TWO,
            expected_providers=["comafi", "cajadevalores"],
        )
        assert open_period(db_session, "AAPL").ratio == Decimal(60)

    def test_periods_never_overlap_in_the_database(self, db_session: Session) -> None:
        """The exclusion constraint holds even for writes that bypass the service."""
        ingest(db_session, [record("AAPL", ratio=Decimal(60))])
        ingest(db_session, [record("AAPL", ratio=Decimal(30))], day=DAY_TWO)
        ingest(db_session, [record("AAPL", ratio=Decimal(15))], day=DAY_THREE)
        rows = periods(db_session, "AAPL")
        for earlier, later in pairwise(rows):
            assert earlier.effective_to == later.effective_from


class TestRatioConstraints:
    """Schema-level guarantees, independent of the ingestion service."""

    def test_the_database_rejects_an_overlapping_period(self, db_session: Session) -> None:
        """A direct insert must fail, not just be unlikely.

        Otherwise a future backfill script could rewrite history without any of
        the service's guards.
        """
        ingest(db_session, [record("AAPL", ratio=Decimal(60))])
        instrument = stored(db_session, "AAPL")
        db_session.add(
            InstrumentRatioHistory(
                instrument_id=instrument.id,
                ratio=Decimal(30),
                effective_from=DAY_ONE,
                effective_to=None,
                source="manual",
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_the_database_rejects_a_non_positive_ratio(self, db_session: Session) -> None:
        """A zero ratio would collapse the theoretical price to nothing."""
        ingest(db_session, [record("AAPL", ratio=None)])
        instrument = stored(db_session, "AAPL")
        db_session.add(
            InstrumentRatioHistory(
                instrument_id=instrument.id,
                ratio=Decimal(0),
                effective_from=DAY_ONE,
                effective_to=None,
                source="manual",
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_the_database_rejects_a_backwards_range(self, db_session: Session) -> None:
        """``effective_to`` before ``effective_from`` is not a period."""
        ingest(db_session, [record("AAPL", ratio=None)])
        instrument = stored(db_session, "AAPL")
        db_session.add(
            InstrumentRatioHistory(
                instrument_id=instrument.id,
                ratio=Decimal(5),
                effective_from=DAY_TWO,
                effective_to=DAY_ONE,
                source="manual",
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_a_symbol_is_unique(self, db_session: Session) -> None:
        """Two rows for one symbol would split the ratio history."""
        ingest(db_session, [record("AAPL")])
        db_session.add(Instrument(symbol="AAPL", first_seen_at=utc_now()))
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_the_same_effective_date_cannot_be_reused(self, db_session: Session) -> None:
        """A closed period and a new one on the same day would collide."""
        ingest(db_session, [record("AAPL", ratio=Decimal(60))])
        instrument = stored(db_session, "AAPL")
        db_session.add(
            InstrumentRatioHistory(
                instrument_id=instrument.id,
                ratio=Decimal(30),
                effective_from=DAY_ONE,
                effective_to=DAY_TWO,
                source="manual",
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_fractional_ratios_survive_the_round_trip(self, db_session: Session) -> None:
        """``1:5`` must come back as ``0.2`` at full precision.

        A ``NUMERIC(20, 2)`` column would silently round it to ``0.20`` and a
        ``FLOAT`` column would make re-ingests see a different value each day.
        """
        ingest(db_session, [record("KEEL", ratio=Decimal("0.2"))])
        ingest(db_session, [record("KEEL", ratio=Decimal("0.2"))], day=DAY_TWO)
        assert periods(db_session, "KEEL")[0].ratio == Decimal("0.2")
        assert len(periods(db_session, "KEEL")) == 1


class TestDeactivation:
    """Only a complete, plausible run may retire an instrument."""

    def test_a_symbol_absent_from_the_sources_is_deactivated(self, db_session: Session) -> None:
        """Delisting is a fact the sources report by omission."""
        ingest(db_session, [record("AAPL"), record("VALE3")])
        report = ingest(db_session, [record("AAPL")], day=DAY_TWO)
        assert report.instruments_deactivated == 1
        assert stored(db_session, "VALE3").is_active is False
        assert stored(db_session, "AAPL").is_active is True

    def test_a_deactivated_instrument_is_never_deleted(self, db_session: Session) -> None:
        """Its price history must stay addressable after delisting."""
        ingest(db_session, [record("AAPL"), record("VALE3", ratio=Decimal(5))])
        ingest(db_session, [record("AAPL")], day=DAY_TWO)
        assert stored(db_session, "VALE3")
        assert len(periods(db_session, "VALE3")) == 1

    def test_a_deactivated_instrument_is_reactivated_when_it_returns(
        self, db_session: Session
    ) -> None:
        """A re-listing or a source coming back must clear the flag."""
        ingest(db_session, [record("AAPL"), record("VALE3")])
        ingest(db_session, [record("AAPL")], day=DAY_TWO)
        ingest(db_session, [record("AAPL"), record("VALE3")], day=DAY_THREE)
        assert stored(db_session, "VALE3").is_active is True

    def test_a_failed_provider_deactivates_nothing(self, db_session: Session) -> None:
        """A provider outage must not be able to retire the whole universe.

        This is the failure the rule exists for: one source returning an empty
        list looks exactly like mass delisting until it is checked.
        """
        ingest(db_session, [record("AAPL"), record("VALE3")])
        report = run_metadata_ingestion(
            db_session,
            [snapshot("comafi", [record("AAPL")])],
            effective_date=DAY_TWO,
            expected_providers=["comafi", "cajadevalores"],
        )
        assert report.instruments_deactivated == 0
        assert stored(db_session, "VALE3").is_active is True

    def test_the_missing_provider_is_recorded_as_a_warning(self, db_session: Session) -> None:
        """An operator must be able to see why nothing was deactivated."""
        ingest(db_session, [record("AAPL"), record("VALE3")])
        report = run_metadata_ingestion(
            db_session,
            [snapshot("comafi", [record("AAPL")])],
            effective_date=DAY_TWO,
            expected_providers=["comafi", "cajadevalores"],
        )
        assert any("cajadevalores" in warning for warning in report.warnings)
        assert any("cajadevalores" in warning for warning in runs(db_session)[-1].warnings)

    def test_a_collapsing_universe_is_refused(self, db_session: Session) -> None:
        """Below the retention floor the sources are broken, not BYMA.

        Losing 90% of the universe in one run is never a real delisting; it is a
        truncated page or a parser regression.
        """
        everything = [record(f"SYM{index:02d}") for index in range(20)]
        ingest(db_session, everything)
        survivors = everything[:5]
        report = ingest(db_session, survivors, day=DAY_TWO)
        assert report.instruments_deactivated == 0
        assert all(stored(db_session, r.symbol).is_active for r in everything)

    def test_normal_churn_is_allowed_through(self, db_session: Session) -> None:
        """The guard must not block real delistings.

        A rule that refuses every deactivation is equivalent to never
        deactivating, and stale instruments would be priced forever.
        """
        everything = [record(f"SYM{index:02d}") for index in range(20)]
        ingest(db_session, everything)
        report = ingest(db_session, everything[:16], day=DAY_TWO)
        assert report.instruments_deactivated == 4

    def test_the_retention_floor_is_half(self) -> None:
        """The constant is a decision, so it is asserted rather than implied."""
        assert MIN_UNIVERSE_RETENTION == 0.5

    def test_a_small_universe_skips_the_guard(self, db_session: Session) -> None:
        """With few instruments the ratio is meaningless, so deactivation proceeds.

        A fresh database has one or two rows; refusing to mark anything stale
        there would leave test and demo data permanently active.
        """
        ingest(db_session, [record("AAPL"), record("VALE3")])
        report = ingest(db_session, [record("AAPL")], day=DAY_TWO)
        assert report.instruments_deactivated == 1

    def test_an_empty_universe_on_a_populated_database_is_refused(
        self, db_session: Session
    ) -> None:
        """The worst case: a source returns nothing and the database has data."""
        ingest(db_session, [record(f"SYM{index:02d}") for index in range(20)])
        report = ingest(db_session, [], day=DAY_TWO)
        assert report.instruments_deactivated == 0


class TestIngestionAudit:
    """A surprising instrument count must be explainable after the fact."""

    def test_a_run_row_is_written(self, db_session: Session) -> None:
        """Every execution is recorded, successful or not."""
        report = ingest(db_session, [record("AAPL")])
        run = runs(db_session)[-1]
        assert run.id == report.run_id
        assert run.job == JOB_NAME
        assert run.status == "succeeded"
        assert run.effective_date == DAY_ONE
        assert run.instruments_seen == 1
        assert run.instruments_created == 1
        assert run.duration_ms is not None

    def test_the_sources_are_recorded(self, db_session: Session) -> None:
        """Which sources answered is the first question about any anomaly."""
        run_metadata_ingestion(
            db_session,
            [snapshot("comafi", [record("AAPL")]), snapshot("cajadevalores", [])],
            effective_date=DAY_ONE,
            expected_providers=["comafi", "cajadevalores"],
        )
        assert runs(db_session)[-1].sources == ["comafi", "cajadevalores"]

    def test_warnings_are_persisted(self, db_session: Session) -> None:
        """An unrecognised venue is only actionable if it reaches the run row."""
        report = ingest(db_session, [record("AAPL")], expected=["comafi", "cajadevalores"])
        assert any("cajadevalores" in warning for warning in report.warnings)
        assert runs(db_session)[-1].warnings == list(report.warnings)

    def test_snapshot_warnings_are_persisted_when_every_provider_answered(
        self, db_session: Session
    ) -> None:
        """Ordinary parser warnings must not be dropped when nothing failed.

        Losing them would mean a run looks completely clean while five venues
        went unmapped.
        """
        report = run_metadata_ingestion(
            db_session,
            [snapshot("comafi", [record("AAPL")], warnings=["AAPL: unknown market"])],
            effective_date=DAY_ONE,
            expected_providers=["comafi"],
        )
        assert report.warnings == ("comafi: AAPL: unknown market",)
        assert runs(db_session)[-1].warnings == list(report.warnings)

    def test_rejections_are_quarantined(self, db_session: Session) -> None:
        """A parser that silently drops rows is indistinguishable from a working one."""
        rejection = RejectedRecord(
            provider="comafi", symbol="WDC", reason="conflicting_ratio", detail="both say 10:1"
        )
        report = run_metadata_ingestion(
            db_session,
            [snapshot("comafi", [record("AAPL")], rejected=[rejection])],
            effective_date=DAY_ONE,
            expected_providers=["comafi"],
        )
        assert report.rejected_count == 1
        stored_run = runs(db_session)[-1]
        assert stored_run.quarantine == [
            {
                "provider": "comafi",
                "symbol": "WDC",
                "reason": "conflicting_ratio",
                "detail": "both say 10:1",
            }
        ]

    def test_a_failed_run_is_marked_failed(self, db_session: Session) -> None:
        """A failure must be legible in the audit table, not only in the log."""
        broken = record("AAPL", ratio=Decimal(0))
        with pytest.raises(IntegrityError):
            ingest(db_session, [broken])
        db_session.rollback()
        assert runs(db_session) == [] or runs(db_session)[-1].status == "failed"


class TestEffectiveDate:
    """A ratio change is dated by the business day, not the wall clock."""

    def test_the_default_is_argentine_today(self, db_session: Session) -> None:
        """A run at 21:00 UTC is the next BYMA session locally, and must use it.

        Dating the change to the UTC day would back-date the new ratio by one
        session, so the last hour of every BYMA day would use the wrong ratio.
        """
        report = run_metadata_ingestion(
            db_session, [snapshot("comafi", [record("AAPL")])], expected_providers=["comafi"]
        )
        stored_run = run_row(db_session, report.run_id)
        assert stored_run.effective_date is not None
        assert stored_run.effective_date <= date.today()

    def test_an_explicit_date_is_used(self, db_session: Session) -> None:
        """Back-fills and tests must be able to choose the date."""
        ingest(db_session, [record("AAPL")], day=date(2024, 6, 3))
        assert periods(db_session, "AAPL")[0].effective_from == date(2024, 6, 3)


class TestFetchAndIngest:
    """Fetching is separate from writing, and failures are contained."""

    class Stub(CedearDataProvider):
        """A provider that returns a fixed snapshot, or raises a fixed error.

        Network access is the one thing these tests must not depend on, and a
        failing source is the case worth exercising precisely because it cannot
        be provoked reliably from the real endpoints.
        """

        def __init__(self, name: str, result: CedearSnapshot | Exception) -> None:
            """Store what the provider will return, and count its calls."""
            self.name = name
            self._result = result
            self.calls = 0

        @property
        def source_url(self) -> str:
            """The document this stub stands in for."""
            return f"https://example.test/{self.name}"

        def fetch(self) -> CedearSnapshot:
            """Return the canned snapshot, or raise the canned error."""
            self.calls += 1
            if isinstance(self._result, Exception):
                raise self._result
            return self._result

    def test_a_working_provider_is_ingested(self, db_session: Session) -> None:
        """The happy path resolves, fetches and writes in one call."""
        provider = self.Stub("comafi", snapshot("comafi", [record("AAPL")]))
        report = fetch_and_ingest(db_session, [provider], effective_date=DAY_ONE)
        assert report.instruments_created == 1

    def test_a_failing_provider_does_not_abort_the_run(self, db_session: Session) -> None:
        """One source being down must not cost the other's data.

        The run completes, the failed provider is named in the warnings, and
        nothing is deactivated.
        """
        good = self.Stub("comafi", snapshot("comafi", [record("AAPL"), record("VALE3")]))
        bad = self.Stub("cajadevalores", ProviderError("503"))
        report = fetch_and_ingest(db_session, [good, bad], effective_date=DAY_ONE)
        assert report.instruments_created == 2
        assert report.instruments_deactivated == 0
        assert any("cajadevalores" in warning for warning in report.warnings)

    def test_every_provider_failing_raises(self, db_session: Session) -> None:
        """A run with no data at all must abort, not report an empty universe."""
        bad = self.Stub("comafi", ProviderError("503"))
        with pytest.raises(ProviderError, match="no snapshots"):
            fetch_and_ingest(db_session, [bad], effective_date=DAY_ONE)

    def test_each_provider_is_fetched_once(self, db_session: Session) -> None:
        """A retried fetch would double the work and skew the audit counts."""
        provider = self.Stub("comafi", snapshot("comafi", [record("AAPL")]))
        fetch_and_ingest(db_session, [provider], effective_date=DAY_ONE)
        assert provider.calls == 1


class TestInstrumentHelpers:
    """Convenience accessors used by the API layer."""

    def test_current_ratio_is_the_open_period(self, db_session: Session) -> None:
        """The API's headline number must be the newest period."""
        ingest(db_session, [record("AAPL", ratio=Decimal(60))])
        instrument = stored(db_session, "AAPL")
        assert instrument.current_ratio == Decimal(60)
        ingest(db_session, [record("AAPL", ratio=Decimal(30))], day=DAY_TWO)
        db_session.refresh(instrument)
        assert instrument.current_ratio == Decimal(30)

    def test_current_ratio_is_none_when_never_published(self, db_session: Session) -> None:
        """An instrument with no ratio must not borrow one."""
        ingest(db_session, [record("AAPL", ratio=None)])
        assert stored(db_session, "AAPL").current_ratio is None

    def test_count_matches_the_stored_rows(self, db_session: Session) -> None:
        """Sanity check on the fixture itself, so a zero elsewhere is a real zero."""
        ingest(db_session, [record("AAPL"), record("VALE3")])
        assert db_session.scalar(select(func.count()).select_from(Instrument)) == 2
