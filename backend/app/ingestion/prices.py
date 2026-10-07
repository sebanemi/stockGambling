"""Price ingestion: fetch daily bars, FX, and store them keyed on market date.

This is the write path for Phase 3. It mirrors the metadata service in shape -
synchronous, session-bound, audit row per run - but the semantics differ in a
way that matters:

* **Re-ingest is idempotent and in-place.** Prices get revised by vendors (a
  corrected close, a restated ``adjusted_close``), so the same
  ``(instrument, market_date)`` is updated, never duplicated. There is no
  "period" concept for prices to protect, unlike ratios.
* **A failed symbol never looks like a quiet one.** If the local or the
  underlying fetch raises for an instrument, a warning is recorded with that
  symbol; nothing is invented.
* **Nothing is ever deactivated here.** A symbol that stops trading simply
  stops getting new bars; its history - and its metadata row - survive.

The as-of discipline is *not* enforced here. Bars are stored per market date in
their own tables; joining a BYMA bar to an underlying bar happens only in
:mod:`app.alignment`, under test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.time import to_market_date, utc_now
from app.domain.vocabulary import UnderlyingMarket
from app.models.instrument import IngestionRun, Instrument
from app.models.market_data import FxRate, LocalPriceBar, UnderlyingPriceBar
from app.providers.base import (
    DailyBar,
    FXDataProvider,
    FxSnapshot,
    LocalPriceDataProvider,
    ProviderError,
    UnderlyingDataProvider,
)

logger = get_logger(__name__)

#: Name recorded on ``sg_ingestion_runs.job``.
JOB_NAME = "ingest.cedear_prices"

#: Quantisation for stored prices, matching ``NUMERIC(20, 6)``. Values are
#: compared in their stored form so a re-ingest of an unchanged bar is not
#: reported as an update.
_PRICE_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class PriceIngestionReport:
    """What one price run did, for the Celery result and the log."""

    run_id: int
    status: str
    sources: tuple[str, ...]
    instruments: tuple[str, ...]
    local_bars_inserted: int
    local_bars_updated: int
    underlying_bars_inserted: int
    underlying_bars_updated: int
    fx_bars_inserted: int
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """JSON-serialisable form, used as the Celery task return value."""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "sources": list(self.sources),
            "instruments": list(self.instruments),
            "local_bars_inserted": self.local_bars_inserted,
            "local_bars_updated": self.local_bars_updated,
            "underlying_bars_inserted": self.underlying_bars_inserted,
            "underlying_bars_updated": self.underlying_bars_updated,
            "fx_bars_inserted": self.fx_bars_inserted,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class _Bars:
    """Mutable tally for a single symbol's price refresh."""

    inserted: int = 0
    updated: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _SymbolRefresh:
    """Per-symbol tallies kept apart by series.

    The local and underlying counts stay separate so the run report can say
    which series contributed what.
    """

    local: _Bars = field(default_factory=_Bars)
    underlying: _Bars = field(default_factory=_Bars)


def _quantise(value: Decimal | None) -> Decimal | None:
    """A price as it will sit in the ``NUMERIC(20, 6)`` column.

    PostgreSQL rounds half away from zero, so a re-ingest must compare halves
    the same way or every bar would report a spurious update.
    """
    if value is None:
        return None
    return value.quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_UP)


def _upsert_daily_bars(
    session: Session,
    model: type[LocalPriceBar] | type[UnderlyingPriceBar],
    instrument: Instrument,
    bars: list[DailyBar],
    tally: _Bars,
) -> None:
    """Store daily bars keyed on ``(instrument_id, market_date)``.

    A date that already has a row is updated in place when a value changed;
    ``tally.updated`` counts only bars that materially changed so a quiet
    re-ingest reports nothing.
    """
    rows = session.scalars(select(model).where(model.instrument_id == instrument.id)).all()
    existing: dict[date, LocalPriceBar | UnderlyingPriceBar] = {
        row.market_date: row for row in cast("list[LocalPriceBar | UnderlyingPriceBar]", rows)
    }
    for bar in bars:
        row = existing.get(bar.market_date)
        if row is None:
            session.add(
                model(
                    instrument_id=instrument.id,
                    symbol=bar.symbol,
                    market_date=bar.market_date,
                    timestamp=bar.timestamp,
                    open=_quantise(bar.open),
                    high=_quantise(bar.high),
                    low=_quantise(bar.low),
                    close=_quantise(bar.close),
                    adjusted_close=_quantise(bar.adjusted_close),
                    volume=bar.volume,
                    traded_value=_quantise(bar.traded_value),
                    trades=bar.trades,
                    currency=bar.currency,
                    source=bar.source,
                )
            )
            tally.inserted += 1
        else:
            changed = False
            for attribute in ("open", "high", "low", "close", "adjusted_close"):
                incoming = _quantise(getattr(bar, attribute))
                if getattr(row, attribute) != incoming:
                    setattr(row, attribute, incoming)
                    changed = True
            row.symbol = bar.symbol
            row.timestamp = bar.timestamp
            row.volume = bar.volume
            row.traded_value = _quantise(bar.traded_value)
            row.trades = bar.trades
            if changed:
                tally.updated += 1


def _upsert_fx_bars(
    session: Session,
    pair: str,
    snapshot: FxSnapshot,
    tally: _Bars,
) -> None:
    """Store FX observations keyed on ``(pair, source, market_date)``."""
    rows = session.scalars(
        select(FxRate).where(FxRate.pair == pair, FxRate.source == snapshot.provider)
    ).all()
    existing: dict[date, FxRate] = {row.market_date: row for row in cast("list[FxRate]", rows)}
    for bar in snapshot.bars:
        row = existing.get(bar.market_date)
        if row is None:
            session.add(
                FxRate(
                    pair=bar.pair,
                    market_date=bar.market_date,
                    timestamp=bar.timestamp,
                    open=_quantise(bar.open),
                    high=_quantise(bar.high),
                    low=_quantise(bar.low),
                    close=_quantise(bar.close),
                    currency=bar.currency,
                    source=bar.source,
                    volume=bar.volume,
                )
            )
            tally.inserted += 1
        else:
            changed = False
            for attribute in ("open", "high", "low", "close"):
                incoming = _quantise(getattr(bar, attribute))
                if getattr(row, attribute) != incoming:
                    setattr(row, attribute, incoming)
                    changed = True
            row.timestamp = bar.timestamp
            row.volume = bar.volume
            if changed:
                tally.updated += 1


def refresh_instrument_prices(
    session: Session,
    instrument: Instrument,
    *,
    local_provider: LocalPriceDataProvider,
    underlying_provider: UnderlyingDataProvider,
    start: date,
    end: date,
) -> _SymbolRefresh:
    """Fetch and store one instrument's local and underlying bars for the window.

    A :class:`ProviderError` from either fetch is converted into a per-symbol
    warning so one broken listing cannot abort a universe refresh; the missing
    symbol is then visibly absent from the run's counts.
    """
    refresh = _SymbolRefresh()

    try:
        local = local_provider.fetch_daily_bars(instrument.symbol, start, end)
    except ProviderError as exc:
        refresh.local.warnings.append(f"{instrument.symbol}: local fetch failed: {exc}")
        logger.error(
            "ingestion.local_failed",
            symbol=instrument.symbol,
            provider=local_provider.name,
            error=str(exc),
        )
    else:
        _upsert_daily_bars(session, LocalPriceBar, instrument, list(local.bars), refresh.local)
        refresh.local.warnings.extend(local.warnings or ())

    market = _underlying_market(instrument, refresh.underlying)
    if market is None:
        return refresh

    try:
        underlying = underlying_provider.fetch_daily_bars(
            instrument.underlying_symbol or "",
            market,
            start,
            end,
        )
    except ProviderError as exc:
        refresh.underlying.warnings.append(f"{instrument.symbol}: underlying fetch failed: {exc}")
        logger.error(
            "ingestion.underlying_failed",
            symbol=instrument.symbol,
            provider=underlying_provider.name,
            error=str(exc),
        )
    else:
        _upsert_daily_bars(
            session, UnderlyingPriceBar, instrument, list(underlying.bars), refresh.underlying
        )
        refresh.underlying.warnings.extend(underlying.warnings or ())

    return refresh


def _underlying_market(instrument: Instrument, tally: _Bars) -> UnderlyingMarket | None:
    """The normalised venue of the instrument's underlying, if fetchable.

    Records a warning when the stored reference is unusable (missing ticker or
    ``UNKNOWN`` venue) rather than inventing one: a series that cannot be routed
    must be a visible gap, not a guessed market.
    """
    if not instrument.underlying_symbol or not instrument.underlying_symbol.strip():
        tally.warnings.append(
            f"{instrument.symbol}: no underlying symbol; underlying series not fetched"
        )
        return None
    if not instrument.underlying_market:
        tally.warnings.append(
            f"{instrument.symbol}: no underlying market; underlying series not fetched"
        )
        return None
    market = UnderlyingMarket(instrument.underlying_market)
    if market is UnderlyingMarket.UNKNOWN:
        tally.warnings.append(
            f"{instrument.symbol}: underlying market is UNKNOWN; series not fetched"
        )
        return None
    return market


def refresh_fx(
    session: Session,
    fx_provider: FXDataProvider,
    *,
    pair: str,
    start: date,
    end: date,
) -> _Bars:
    """Fetch and store the FX series for the window, returning a tally."""
    tally = _Bars()
    try:
        snapshot = fx_provider.fetch_daily(pair, start, end)
    except ProviderError as exc:
        tally.warnings.append(f"{pair}: FX fetch failed: {exc}")
        logger.error("ingestion.fx_failed", pair=pair, provider=fx_provider.name, error=str(exc))
    else:
        _upsert_fx_bars(session, pair, snapshot, tally)
        tally.warnings.extend(snapshot.warnings or ())
    return tally


def backfill_window(days: int) -> tuple[date, date]:
    """The date window a backfill job covers, ending on the current BYMA date.

    ``days=0`` yields an empty window; a negative ``days`` is nonsense and is
    treated as the caller misreading the setting.
    """
    end = to_market_date(utc_now())
    start = end - timedelta(days=max(days, 0))
    return start, end


def run_market_ingestion(
    session: Session,
    instruments: list[Instrument],
    *,
    local_provider: LocalPriceDataProvider,
    underlying_provider: UnderlyingDataProvider,
    fx_provider: FXDataProvider,
    start: date,
    end: date,
    fx_pair: str = "USDARS",
    job: str = JOB_NAME,
) -> PriceIngestionReport:
    """Refresh prices for ``instruments`` and the FX pair, with one audit row.

    The caller owns the transaction; nothing here commits. ``instruments`` is
    deliberately a list (a database query, not a lazy collection) so the scope
    is pinned before the loop starts.
    """
    symbols = [instrument.symbol for instrument in instruments]
    sources = (local_provider.name, underlying_provider.name, fx_provider.name)
    run = IngestionRun(
        job=job,
        status="running",
        effective_date=to_market_date(utc_now()),
        sources=list(sources),
        instruments_seen=len(instruments),
    )
    session.add(run)
    session.flush()

    tally_local = _Bars()
    tally_underlying = _Bars()
    tally_fx = _Bars()
    warnings: list[str] = []

    try:
        for instrument in instruments:
            symbol_tally = refresh_instrument_prices(
                session,
                instrument,
                local_provider=local_provider,
                underlying_provider=underlying_provider,
                start=start,
                end=end,
            )
            tally_local.inserted += symbol_tally.local.inserted
            tally_local.updated += symbol_tally.local.updated
            tally_underlying.inserted += symbol_tally.underlying.inserted
            tally_underlying.updated += symbol_tally.underlying.updated
            warnings.extend(symbol_tally.local.warnings)
            warnings.extend(symbol_tally.underlying.warnings)

        fx_counts = refresh_fx(session, fx_provider, pair=fx_pair, start=start, end=end)
        tally_fx.inserted += fx_counts.inserted
        tally_fx.updated += fx_counts.updated
        warnings.extend(fx_counts.warnings)
    except Exception as exc:
        run.status = "failed"
        run.finished_at = utc_now()
        run.error = f"{type(exc).__name__}: {exc}"
        run.warnings = warnings
        session.flush()
        logger.error("ingestion.prices_failed", job=job, run_id=run.id, error=str(exc))
        raise

    run.finished_at = utc_now()
    run.status = "succeeded"
    run.warnings = warnings
    session.flush()

    report = PriceIngestionReport(
        run_id=run.id,
        status="succeeded",
        sources=sources,
        instruments=tuple(symbols),
        local_bars_inserted=tally_local.inserted,
        local_bars_updated=tally_local.updated,
        underlying_bars_inserted=tally_underlying.inserted,
        underlying_bars_updated=tally_underlying.updated,
        fx_bars_inserted=tally_fx.inserted,
        warnings=tuple(warnings),
    )
    logger.info("ingestion.prices_completed", **report.as_dict())
    return report


__all__ = [
    "JOB_NAME",
    "PriceIngestionReport",
    "backfill_window",
    "refresh_fx",
    "refresh_instrument_prices",
    "run_market_ingestion",
]
