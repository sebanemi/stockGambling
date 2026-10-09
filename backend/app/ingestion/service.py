"""Metadata ingestion: reconcile provider snapshots into the database.

This is the write path for the CEDEAR universe. It is an **upsert** keyed on
the BYMA symbol; there is no hardcoded list of CEDEARs anywhere in the
codebase, so a newly listed program appears because a source published it.

Three invariants drive the design, and each one has a corresponding Phase 2
exit criterion:

* **A past date never inherits today's ratio.** Ratios live in their own table
  keyed by ``[effective_from, effective_to)``. A change closes the open period
  and opens a new one; historical periods are never rewritten. A re-ingest that
  discovers today's ratio therefore cannot alter what a 2024 back-test used.
* **Re-running is a no-op.** A ratio equal to the open period's value does not
  open a new period, so a daily job does not manufacture a period per day.
* **A failure never deactivates.** Instruments are only marked inactive when
  *every* configured provider returned a snapshot and the resulting universe is
  not implausibly smaller than what is already stored. A provider outage that
  returns an empty list must not be able to retire the entire universe.

The service is deliberately synchronous and session-bound so it can be called
from a Celery task, a management script or a test with equal confidence.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.time import to_market_date, utc_now
from app.domain.vocabulary import format_ratio
from app.ingestion.reconcile import MergedUniverse, merge_snapshots
from app.models.instrument import IngestionRun, Instrument, InstrumentRatioHistory
from app.providers.base import CedearDataProvider, CedearRecord, CedearSnapshot, ProviderError

logger = get_logger(__name__)

#: Name recorded on ``sg_ingestion_runs.job``.
JOB_NAME = "ingest.cedear_metadata"

#: A run whose universe collapses below this fraction of what is already stored
#: is treated as an upstream regression, not as mass delisting, and is refused
#: before anything is deactivated. Below 50% the source is broken, not the
#: exchange.
MIN_UNIVERSE_RETENTION = 0.5

#: Below this many instruments the retention check is meaningless (a fresh
#: database, or a test), so it is skipped.
RETENTION_CHECK_MIN_INSTRUMENTS = 10


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """What one ingestion run did, for the Celery result and the log."""

    run_id: int
    sources: tuple[str, ...]
    instruments_seen: int
    instruments_created: int
    instruments_updated: int
    instruments_deactivated: int
    ratios_inserted: int
    rejected_count: int
    warnings: tuple[str, ...] = ()
    status: str = "succeeded"

    def as_dict(self) -> dict[str, object]:
        """JSON-serialisable form, used as the Celery task return value."""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "sources": list(self.sources),
            "instruments_seen": self.instruments_seen,
            "instruments_created": self.instruments_created,
            "instruments_updated": self.instruments_updated,
            "instruments_deactivated": self.instruments_deactivated,
            "ratios_inserted": self.ratios_inserted,
            "rejected_count": self.rejected_count,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class _Counters:
    """Mutable tally for a single run."""

    created: int = 0
    updated: int = 0
    deactivated: int = 0
    ratios_inserted: int = 0
    #: Symbols whose stored state did not change at all.
    unchanged: list[str] = field(default_factory=list)


def run_metadata_ingestion(
    session: Session,
    snapshots: Sequence[CedearSnapshot],
    *,
    effective_date: date | None = None,
    expected_providers: Iterable[str] | None = None,
    job: str = JOB_NAME,
) -> IngestionReport:
    """Upsert the reconciled universe and apply any ratio changes.

    Args:
        session: An open session. The caller owns the transaction; this
            function does not commit.
        snapshots: One snapshot per provider that succeeded. A provider that
            raised is simply absent, and its instruments are left untouched.
        effective_date: The business date the new ratio periods start on.
            Defaults to today in Argentine time, not UTC, so a run at 21:00 UTC
            does not date a ratio to the next BYMA session.
        expected_providers: Every provider the run was meant to consult. When
            given and a name is missing from ``snapshots``, nothing is
            deactivated.
        job: Recorded on the ingestion-run audit row.

    Returns:
        An :class:`IngestionReport`. The report is written to
        ``sg_ingestion_runs`` before it is returned.
    """
    if not snapshots:
        raise ProviderError(
            "refusing to run metadata ingestion with no snapshots: a failed fetch must "
            "never be allowed to look like an empty universe"
        )

    effective_date = effective_date or to_market_date(utc_now())
    sources = tuple(snapshot.provider for snapshot in snapshots)
    missing = set(expected_providers or ()) - set(sources)

    started = utc_now()
    universe = merge_snapshots(snapshots)
    run = IngestionRun(
        job=job,
        status="running",
        effective_date=effective_date,
        sources=list(sources),
        instruments_seen=len(universe),
    )
    session.add(run)
    session.flush()

    counters = _Counters()
    run_warnings = list(universe.warnings)
    if missing:
        run_warnings.append(
            f"providers {sorted(missing)} did not return a snapshot; no instrument was deactivated"
        )
    run.warnings = run_warnings
    try:
        for item in universe.instruments:
            _upsert_instrument(session, item.record, counters)
        # Newly added instruments must be visible to the ratio step below.
        # Production sessions run with autoflush=False, so without this flush
        # a first ingest would upsert the universe and silently open zero
        # ratio periods.
        session.flush()
        _apply_ratio_changes(session, universe, effective_date, counters)

        if missing:
            counters.deactivated = 0
        else:
            counters.deactivated = _deactivate_missing(
                session, {item.record.symbol for item in universe.instruments}
            )
    except Exception as exc:
        run.status = "failed"
        run.finished_at = utc_now()
        run.error = f"{type(exc).__name__}: {exc}"
        run.quarantine = [rejection.as_dict() for rejection in universe.rejected]
        session.flush()
        logger.error("ingestion.failed", job=job, run_id=run.id, error=str(exc))
        raise

    run.instruments_created = counters.created
    run.instruments_updated = counters.updated
    run.instruments_deactivated = counters.deactivated
    run.ratios_inserted = counters.ratios_inserted
    run.quarantine = [rejection.as_dict() for rejection in universe.rejected]
    finished = utc_now()
    run.status = "succeeded"
    run.finished_at = finished
    run.duration_ms = int((finished - started).total_seconds() * 1000)
    session.flush()

    report = IngestionReport(
        run_id=run.id,
        sources=sources,
        instruments_seen=len(universe),
        instruments_created=counters.created,
        instruments_updated=counters.updated,
        instruments_deactivated=counters.deactivated,
        ratios_inserted=counters.ratios_inserted,
        rejected_count=len(universe.rejected),
        warnings=tuple(run_warnings),
    )
    logger.info("ingestion.completed", **report.as_dict())
    return report


def _upsert_instrument(session: Session, record: CedearRecord, counters: _Counters) -> None:
    """Create or refresh one instrument, keyed on its BYMA symbol.

    Fields are only overwritten by a value that carries information. The typed
    columns default to ``UNKNOWN``/``None`` and a source that does not publish a
    venue this run must not erase a venue another source published earlier;
    "I am silent" is not "this changed".
    """
    symbol = record.symbol

    instrument = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
    now = utc_now()

    if instrument is None:
        instrument = Instrument(symbol=symbol, first_seen_at=now)
        session.add(instrument)
        counters.created += 1
    else:
        counters.updated += 1

    instrument.name = record.name or instrument.name
    instrument.instrument_type = _keep_informed(
        record.instrument_type.value, instrument.instrument_type
    )
    instrument.underlying_symbol = record.underlying_symbol or instrument.underlying_symbol
    instrument.underlying_name = record.underlying_name or instrument.underlying_name
    instrument.underlying_market = _keep_informed(
        record.underlying_market.value, instrument.underlying_market
    )
    instrument.underlying_market_raw = (
        record.underlying_market_raw or instrument.underlying_market_raw
    )
    instrument.isin = record.isin or instrument.isin
    instrument.underlying_isin = record.underlying_isin or instrument.underlying_isin
    instrument.custodian = record.custodian
    instrument.program_status = _keep_informed(
        record.program_status.value, instrument.program_status
    )
    instrument.program_status_raw = record.program_status_raw or instrument.program_status_raw
    instrument.attributes = {
        **(instrument.attributes or {}),
        **{f"sources.{key}": value for key, value in record.extra.items()},
    }
    instrument.is_active = True
    instrument.last_seen_at = now


def _keep_informed(incoming: str, stored: str | None) -> str:
    """Return ``incoming`` unless it is the ``UNKNOWN`` default, which is silence.

    The typed columns carry ``UNKNOWN`` as a deliberate "nobody said" value, so
    a source that omits a venue this run must not erase a venue another source
    published earlier. "I am silent" is not "this changed", and the stored value
    is only replaced by a value that says something.
    """
    if incoming != "UNKNOWN":
        return incoming
    return stored or "UNKNOWN"


def open_ratio_period(session: Session, instrument: Instrument) -> InstrumentRatioHistory | None:
    """Return the instrument's currently open ratio period, if it has one."""
    return session.scalar(
        select(InstrumentRatioHistory).where(
            InstrumentRatioHistory.instrument_id == instrument.id,
            InstrumentRatioHistory.effective_to.is_(None),
        )
    )


def _apply_ratio_changes(
    session: Session,
    universe: MergedUniverse,
    effective_date: date,
    counters: _Counters,
) -> None:
    """Open, close or leave alone ratio periods.

    The decision is made on the *open* period only. Closed periods are history
    and are never touched, which is what makes a re-ingest of a corrected
    historical ratio a visible, auditable event rather than a silent rewrite.
    """
    for item in universe.instruments:
        ratio: Decimal | None = item.record.ratio
        if ratio is None:
            # No ratio this run. That is not evidence the ratio was removed,
            # so the open period is left exactly as it is.
            continue

        instrument = session.scalar(
            select(Instrument).where(Instrument.symbol == item.record.symbol)
        )
        if instrument is None:
            continue

        current = open_ratio_period(session, instrument)
        if current is not None and current.ratio == ratio:
            counters.unchanged.append(instrument.symbol)
            continue

        if current is not None:
            # Half-open periods: the old ratio is in force up to, but not
            # including, the change date, and the new one from that date.
            current.effective_to = effective_date

        session.add(
            InstrumentRatioHistory(
                instrument_id=instrument.id,
                ratio=ratio,
                effective_from=effective_date,
                effective_to=None,
                source=item.primary_source,
                source_ref=item.record.source_ref,
            )
        )
        counters.ratios_inserted += 1
        logger.info(
            "ingestion.ratio_changed",
            symbol=instrument.symbol,
            previous=None if current is None else str(current.ratio),
            previous_formatted=None if current is None else format_ratio(current.ratio),
            ratio=str(ratio),
            ratio_formatted=format_ratio(ratio),
            effective_from=effective_date.isoformat(),
            source=item.primary_source,
        )

    # The exclusion constraint is enforced per statement, so the new rows are
    # only checkable once the closed periods have been flushed.
    session.flush()


def _deactivate_missing(session: Session, seen: set[str]) -> int:
    """Mark instruments no source lists any more as inactive.

    Never deletes: the price history of a delisted CEDEAR stays addressable, and
    ``is_active`` is what the API filters on.

    The retention guard matters because BYMA listings churn and because a
    source that returns a truncated page is indistinguishable from a source
    that reports mass delisting until it is checked.
    """
    stale = session.scalars(
        select(Instrument).where(Instrument.is_active.is_(True), Instrument.symbol.notin_(seen))
    ).all()
    if not stale:
        return 0

    active_total = session.scalar(
        select(func.count()).select_from(Instrument).where(Instrument.is_active.is_(True))
    )
    if active_total is not None and active_total >= RETENTION_CHECK_MIN_INSTRUMENTS:
        retention = (active_total - len(stale)) / active_total
        if retention < MIN_UNIVERSE_RETENTION:
            logger.warning(
                "ingestion.deactivation_refused",
                would_deactivate=len(stale),
                active_total=active_total,
                retention=round(retention, 4),
                minimum=MIN_UNIVERSE_RETENTION,
            )
            return 0

    for instrument in stale:
        instrument.is_active = False
        # last_seen_at is deliberately left alone: this instrument was *not*
        # seen this run. Advancing the timestamp would erase the evidence that
        # it stopped appearing, which is the whole reason the flag exists.
    logger.info("ingestion.deactivated", count=len(stale), symbols=[i.symbol for i in stale])
    return len(stale)


def fetch_and_ingest(
    session: Session,
    providers: Sequence[CedearDataProvider],
    *,
    effective_date: date | None = None,
) -> IngestionReport:
    """Fetch from each provider, then upsert whatever came back.

    A provider that raises is logged and skipped rather than aborting the run,
    but the missing name is passed through as an expected provider so nothing
    is deactivated. A run where *every* provider failed raises.
    """
    snapshots: list[CedearSnapshot] = []
    for provider in providers:
        try:
            snapshots.append(provider.fetch())
        except ProviderError as exc:
            logger.error("ingestion.provider_failed", provider=provider.name, error=str(exc))

    return run_metadata_ingestion(
        session,
        snapshots,
        effective_date=effective_date,
        expected_providers=[provider.name for provider in providers],
    )


def ratio_effective_on(session: Session, instrument: Instrument, day: date) -> Decimal | None:
    """The ratio in force on ``day``, or ``None`` when no period covers it.

    The single lookup used by every later phase. It returns ``None`` rather than
    the nearest period because a missing ratio must surface as a missing value,
    not as a substituted one.
    """
    period = session.scalar(
        select(InstrumentRatioHistory)
        .where(
            InstrumentRatioHistory.instrument_id == instrument.id,
            InstrumentRatioHistory.effective_from <= day,
            or_(
                InstrumentRatioHistory.effective_to.is_(None),
                InstrumentRatioHistory.effective_to > day,
            ),
        )
        .order_by(InstrumentRatioHistory.effective_from.desc())
    )
    return None if period is None else period.ratio


__all__ = [
    "JOB_NAME",
    "IngestionReport",
    "fetch_and_ingest",
    "open_ratio_period",
    "ratio_effective_on",
    "run_metadata_ingestion",
]
