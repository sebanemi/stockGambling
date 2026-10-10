"""Theoretical CEDEAR price ingestion: compute and store theoretical prices.

This is the write path for Phase 4. It mirrors the market-data ingestion in
shape - synchronous, session-bound, audit row per run - but computes the
theoretical price from already-stored local, underlying and FX bars.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.time import to_market_date, utc_now
from app.models.instrument import IngestionRun, Instrument
from app.models.theoretical import TheoreticalPriceBar
from app.theoretical.engine import (
    TheoreticalResult,
    build_theoretical_result,
)

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

JOB_NAME = "ingest.theoretical_prices"

# Quantisation for stored prices, matching NUMERIC(20, 6).
_PRICE_QUANTUM = Decimal("0.000001")


def _quantise(value: Decimal | None) -> Decimal | None:
    """A price as it will sit in the NUMERIC(20, 6) column."""
    if value is None:
        return None
    return value.quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class TheoreticalIngestionReport:
    """What one theoretical price run did, for the Celery result and the log."""

    run_id: int
    status: str
    instruments: tuple[str, ...]
    theoretical_bars_inserted: int
    theoretical_bars_updated: int
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """JSON-serialisable form, used as the Celery task return value."""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "instruments": list(self.instruments),
            "theoretical_bars_inserted": self.theoretical_bars_inserted,
            "theoretical_bars_updated": self.theoretical_bars_updated,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class _Tally:
    """Mutable tally for a single instrument's theoretical refresh."""

    inserted: int = 0
    updated: int = 0
    warnings: list[str] = field(default_factory=list)


def _upsert_theoretical_bar(
    session: Session,
    instrument: Instrument,
    market_date: date,
    result: TheoreticalResult,
    source: str,
    source_ref: str | None,
    tally: _Tally,
) -> None:
    """Store or update a theoretical price bar keyed on (instrument_id, market_date)."""
    existing = session.scalar(
        select(TheoreticalPriceBar).where(
            TheoreticalPriceBar.instrument_id == instrument.id,
            TheoreticalPriceBar.market_date == market_date,
        )
    )

    if existing is None:
        session.add(
            TheoreticalPriceBar(
                instrument_id=instrument.id,
                market_date=market_date,
                theoretical_price=_quantise(result.theoretical_price),
                ratio_used=_quantise(result.ratio_used),
                fx_used=_quantise(result.fx_used),
                underlying_price_used=_quantise(result.underlying_price_used),
                local_price=_quantise(result.local_price),
                premium_discount=_quantise(result.premium_discount),
                underlying_market_date=result.underlying_market_date,
                fx_market_date=result.fx_market_date,
                source=source,
                source_ref=source_ref,
            )
        )
        tally.inserted += 1
    else:
        changed = False
        for attr in (
            "theoretical_price",
            "ratio_used",
            "fx_used",
            "underlying_price_used",
            "local_price",
            "premium_discount",
            "underlying_market_date",
            "fx_market_date",
        ):
            incoming = (
                _quantise(getattr(result, attr))
                if attr
                in (
                    "theoretical_price",
                    "ratio_used",
                    "fx_used",
                    "underlying_price_used",
                    "local_price",
                    "premium_discount",
                )
                else getattr(result, attr)
            )
            if getattr(existing, attr) != incoming:
                setattr(existing, attr, incoming)
                changed = True
        if changed:
            tally.updated += 1


def refresh_instrument_theoretical(
    session: Session,
    instrument: Instrument,
    *,
    prediction_instant: datetime,
    source: str = "computed",
    source_ref: str | None = None,
) -> _Tally:
    """Compute and store one instrument's theoretical price for the given instant.

    A missing input (ratio, underlying bar, FX) becomes a per-symbol warning.
    """
    tally = _Tally()

    result = build_theoretical_result(session, instrument, prediction_instant)
    if result is None:
        tally.warnings.append(f"{instrument.symbol}: missing input for theoretical price")
        logger.warning(
            "ingestion.theoretical_missing_input",
            symbol=instrument.symbol,
        )
        return tally

    market_date = to_market_date(prediction_instant)
    _upsert_theoretical_bar(session, instrument, market_date, result, source, source_ref, tally)

    return tally


def run_theoretical_ingestion(
    session: Session,
    instruments: list[Instrument],
    *,
    prediction_instant: datetime,
    job: str = JOB_NAME,
) -> TheoreticalIngestionReport:
    """Compute theoretical prices for ``instruments`` at ``prediction_instant``.

    The caller owns the transaction; nothing here commits. ``instruments`` is
    deliberately a list (a database query, not a lazy collection) so the scope
    is pinned before the loop starts.
    """
    symbols = [instrument.symbol for instrument in instruments]
    run = IngestionRun(
        job=job,
        status="running",
        effective_date=to_market_date(prediction_instant),
        sources=["computed"],
        instruments_seen=len(instruments),
    )
    session.add(run)
    session.flush()

    tally = _Tally()
    warnings: list[str] = []

    try:
        for instrument in instruments:
            symbol_tally = refresh_instrument_theoretical(
                session,
                instrument,
                prediction_instant=prediction_instant,
                source="computed",
                source_ref=None,
            )
            tally.inserted += symbol_tally.inserted
            tally.updated += symbol_tally.updated
            warnings.extend(symbol_tally.warnings)
    except Exception as exc:
        run.status = "failed"
        run.finished_at = utc_now()
        run.error = f"{type(exc).__name__}: {exc}"
        run.warnings = warnings
        session.flush()
        logger.error("ingestion.theoretical_failed", job=job, run_id=run.id, error=str(exc))
        raise

    run.finished_at = utc_now()
    run.status = "succeeded"
    run.warnings = warnings
    session.flush()

    report = TheoreticalIngestionReport(
        run_id=run.id,
        status="succeeded",
        instruments=tuple(symbols),
        theoretical_bars_inserted=tally.inserted,
        theoretical_bars_updated=tally.updated,
        warnings=tuple(warnings),
    )
    logger.info("ingestion.theoretical_completed", **report.as_dict())
    return report


__all__ = [
    "JOB_NAME",
    "TheoreticalIngestionReport",
    "refresh_instrument_theoretical",
    "run_theoretical_ingestion",
]
