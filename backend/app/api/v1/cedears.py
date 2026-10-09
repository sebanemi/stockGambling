"""CEDEAR metadata read API.

Two endpoints, and both are read-only projections of what ingestion stored:

* ``GET /api/v1/cedears`` - the universe, filtered and paginated.
* ``GET /api/v1/cedears/{symbol}`` - one instrument, its underlying, and its
  **complete ratio history**.
* ``GET /api/v1/cedears/{symbol}/theoretical-price`` - theoretical CEDEAR price
  history with full provenance.
* ``GET /api/v1/cedears/{symbol}/features`` - the live feature snapshot for a
  prediction instant (computed from stored bars, never persisted here).
* ``GET /api/v1/cedears/{symbol}/features/snapshots`` - paginated stored
  snapshots written by the ``feature.build`` worker task.

The response contract follows the project rule that every value traces to
stored data:

* the ratio is rendered in the ``"N:D"`` form the official sources publish, and
  the numeric value is returned alongside it so no consumer has to reverse the
  orientation convention;
* the ratio in force **on a requested date** is a query parameter, not an
  implicit "today". ``?as_of=2024-03-01`` returns the ratio that was in force
  that day, and ``null`` when no period covers it - never the current one.
* ``null`` means "not known", everywhere. It is never backfilled with a
  default, a nearest value or a computed one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import SessionDep
from app.api.v1.common import MAX_PAGE_SIZE, resolve_instrument
from app.core.time import ensure_utc, to_market_date, utc_now
from app.domain.vocabulary import format_ratio
from app.features.build import compute_all_features, feature_version
from app.features.store import (
    count_feature_snapshots,
    decode_feature_values,
    list_feature_snapshots,
)
from app.models.features import FeatureStore
from app.models.instrument import Instrument, InstrumentRatioHistory
from app.models.theoretical import TheoreticalPriceBar

router = APIRouter(prefix="/cedears", tags=["cedears"])


def _default_as_of() -> date:
    """Today on the BYMA calendar, not in UTC.

    A request served at 21:00 UTC is still the same Argentine session, so the
    default ratio date must be the Argentine one; using the UTC date would hand
    back a period that is not yet in force in Buenos Aires.
    """
    return to_market_date(utc_now())


class RatioPeriod(BaseModel):
    """One period during which a conversion ratio was in force."""

    ratio: Decimal = Field(description="CEDEARs per underlying unit, e.g. 60 for 60:1")
    ratio_formatted: str = Field(description="The published 'N:D' form, e.g. '60:1'")
    effective_from: date
    effective_to: date | None = Field(description="Exclusive end; null while still in force")
    source: str
    source_ref: str | None = None


class CedearSummary(BaseModel):
    """A CEDEAR as listed on BYMA, with its underlying reference."""

    symbol: str
    name: str | None = None
    instrument_type: str
    is_active: bool

    underlying_symbol: str | None = None
    underlying_name: str | None = None
    underlying_market: str | None = None
    underlying_market_raw: str | None = None
    underlying_isin: str | None = None

    isin: str | None = None
    custodian: str | None = None
    program_status: str

    #: The ratio in force on the requested ``as_of``, or ``None`` when unknown.
    current_ratio: Decimal | None = Field(
        description="Ratio in force on the requested as_of date, as CEDEARs per underlying unit"
    )
    current_ratio_formatted: str | None = None
    ratio_effective_from: date | None = None
    ratio_effective_to: date | None = None
    ratio_source: str | None = None

    first_seen_at: str
    last_seen_at: str


class CedearListResponse(BaseModel):
    """A page of the CEDEAR universe."""

    items: list[CedearSummary]
    total: int = Field(description="Total instruments matching the filter, ignoring pagination")
    limit: int
    offset: int


class CedearDetailResponse(CedearSummary):
    """One instrument with its full conversion-ratio history."""

    program_status_raw: str | None = None
    #: Every ratio period ever recorded, oldest first. A consumer computing a
    #: historical theoretical price needs all of them, not just the current one.
    ratio_history: list[RatioPeriod]
    attributes: dict[str, Any] = Field(
        description="Unmapped fields, kept per source. Not part of the stable contract."
    )


class TheoreticalPriceBarResponse(BaseModel):
    """One day's theoretical CEDEAR value with full provenance."""

    market_date: date
    theoretical_price: Decimal
    ratio_used: Decimal
    ratio_used_formatted: str
    fx_used: Decimal
    underlying_price_used: Decimal
    local_price: Decimal | None = None
    premium_discount: Decimal | None = None
    underlying_market_date: date
    fx_market_date: date
    source: str
    source_ref: str | None = None
    created_at: str


class TheoreticalPriceListResponse(BaseModel):
    """A page of theoretical CEDEAR prices."""

    items: list[TheoreticalPriceBarResponse]
    total: int = Field(description="Total bars matching the filter, ignoring pagination")
    limit: int
    offset: int


class FeatureSnapshotResponse(BaseModel):
    """One instrument's complete feature vector at a prediction instant."""

    symbol: str
    instrument_id: int
    as_of: str = Field(description="Prediction instant (aware UTC ISO-8601)")
    market_date: date = Field(description="BYMA date the instant falls on")
    feature_version: str
    features: dict[str, float | int | None] = Field(
        description="Feature name to value; null means insufficient history, never a default"
    )


class StoredFeatureSnapshotResponse(BaseModel):
    """One persisted snapshot written by the feature.build worker task."""

    as_of: str
    market_date: date
    feature_version: str
    features: dict[str, float | int | None]
    created_at: str


class FeatureSnapshotListResponse(BaseModel):
    """A page of stored feature snapshots, newest first."""

    items: list[StoredFeatureSnapshotResponse]
    total: int = Field(description="Total snapshots matching the filter, ignoring pagination")
    limit: int
    offset: int


def _fetch_ratio_period(
    session: Session, instrument_id: int, as_of: date
) -> InstrumentRatioHistory | None:
    """Load the ratio period in force on ``as_of`` for one instrument.

    Half-open ``[effective_from, effective_to)``: a change effective on day D
    means D already uses the new ratio. Returns ``None`` rather than the nearest
    period, so a date before the first observation is honestly "unknown".
    """
    return session.scalar(
        select(InstrumentRatioHistory)
        .where(
            InstrumentRatioHistory.instrument_id == instrument_id,
            InstrumentRatioHistory.effective_from <= as_of,
            or_(
                InstrumentRatioHistory.effective_to.is_(None),
                InstrumentRatioHistory.effective_to > as_of,
            ),
        )
        .order_by(InstrumentRatioHistory.effective_from.desc())
    )


def _to_period(row: InstrumentRatioHistory) -> RatioPeriod:
    """Serialise one ratio period."""
    return RatioPeriod(
        ratio=row.ratio,
        ratio_formatted=format_ratio(row.ratio) or "",
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        source=row.source,
        source_ref=row.source_ref,
    )


def _to_summary(instrument: Instrument, period: InstrumentRatioHistory | None) -> CedearSummary:
    """Serialise an instrument together with the ratio valid for the request."""
    return CedearSummary(
        symbol=instrument.symbol,
        name=instrument.name,
        instrument_type=instrument.instrument_type,
        is_active=instrument.is_active,
        underlying_symbol=instrument.underlying_symbol,
        underlying_name=instrument.underlying_name,
        underlying_market=instrument.underlying_market,
        underlying_market_raw=instrument.underlying_market_raw,
        underlying_isin=instrument.underlying_isin,
        isin=instrument.isin,
        custodian=instrument.custodian,
        program_status=instrument.program_status,
        current_ratio=None if period is None else period.ratio,
        current_ratio_formatted=None if period is None else format_ratio(period.ratio),
        ratio_effective_from=None if period is None else period.effective_from,
        ratio_effective_to=None if period is None else period.effective_to,
        ratio_source=None if period is None else period.source,
        first_seen_at=instrument.first_seen_at.isoformat(),
        last_seen_at=instrument.last_seen_at.isoformat(),
    )


@router.get("", response_model=CedearListResponse, summary="List CEDEARs")
@router.get(
    "/",
    response_model=CedearListResponse,
    include_in_schema=False,
    summary="List CEDEARs (trailing slash)",
)
def list_cedears(
    session: SessionDep,
    q: Annotated[
        str | None, Query(description="Case-insensitive substring of symbol or name")
    ] = None,
    instrument_type: Annotated[
        Literal["STOCK", "ETF", "OTHER", "UNKNOWN"] | None, Query(description="Exact type")
    ] = None,
    underlying_market: Annotated[str | None, Query(description="Normalised venue code")] = None,
    underlying_symbol: Annotated[str | None, Query(description="Exact underlying ticker")] = None,
    include_inactive: Annotated[
        bool, Query(description="Also return delisted instruments")
    ] = False,
    as_of: Annotated[
        date | None,
        Query(description="Date the returned ratio must have been in force on. Defaults to today."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CedearListResponse:
    """Return a page of the CEDEAR universe.

    Only instruments the ingestion job has stored are returned. There is no
    hardcoded universe, so a newly listed CEDEAR appears as soon as a source
    publishes it and ingestion runs.
    """
    filters: list[Any] = []
    if not include_inactive:
        filters.append(Instrument.is_active.is_(True))
    if instrument_type is not None:
        filters.append(Instrument.instrument_type == instrument_type)
    if underlying_market is not None:
        filters.append(Instrument.underlying_market == underlying_market.upper())
    if underlying_symbol is not None:
        filters.append(Instrument.underlying_symbol == underlying_symbol.upper())
    if q:
        pattern = f"%{q.strip().upper()}%"
        filters.append(
            or_(
                Instrument.symbol.ilike(pattern),
                func.upper(Instrument.name).like(pattern),
                Instrument.underlying_symbol.ilike(pattern),
            )
        )

    total = session.scalar(select(func.count()).select_from(Instrument).where(*filters)) or 0
    instruments = (
        session.scalars(
            select(Instrument)
            .where(*filters)
            .order_by(Instrument.symbol)
            .limit(limit)
            .offset(offset)
            .options(selectinload(Instrument.ratios))
        )
        .unique()
        .all()
    )

    items = [
        _to_summary(
            instrument, _fetch_ratio_period(session, instrument.id, as_of or _default_as_of())
        )
        for instrument in instruments
    ]

    return CedearListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{symbol}", response_model=CedearDetailResponse, summary="Get one CEDEAR")
def get_cedear(
    session: SessionDep,
    symbol: Annotated[str, Path(min_length=1, max_length=32, description="BYMA ticker")],
    as_of: Annotated[
        date | None,
        Query(description="Date the returned current_ratio must have been in force on."),
    ] = None,
) -> CedearDetailResponse:
    """Return one instrument, its underlying and its complete ratio history.

    Raises:
        HTTPException: ``404`` when the symbol is unknown. An unknown symbol and
            a symbol whose ratio is unknown are different situations and are
            never conflated.
    """
    normalised = symbol.strip().upper()
    instrument = session.scalar(select(Instrument).where(Instrument.symbol == normalised))
    if instrument is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "instrument_not_found",
                "symbol": normalised,
                "message": (
                    f"{normalised} is not in the stored CEDEAR universe. "
                    "Run the metadata ingestion job if it is expected to be listed."
                ),
            },
        )

    history = session.scalars(
        select(InstrumentRatioHistory)
        .where(InstrumentRatioHistory.instrument_id == instrument.id)
        .order_by(InstrumentRatioHistory.effective_from)
    ).all()
    period = _fetch_ratio_period(session, instrument.id, as_of or _default_as_of())

    return CedearDetailResponse(
        **_to_summary(instrument, period).model_dump(),
        program_status_raw=instrument.program_status_raw,
        ratio_history=[_to_period(row) for row in history],
        attributes=instrument.attributes or {},
    )


def _to_theoretical_response(bar: TheoreticalPriceBar) -> TheoreticalPriceBarResponse:
    """Serialise one theoretical price bar."""
    return TheoreticalPriceBarResponse(
        market_date=bar.market_date,
        theoretical_price=bar.theoretical_price,
        ratio_used=bar.ratio_used,
        ratio_used_formatted=format_ratio(bar.ratio_used) or "",
        fx_used=bar.fx_used,
        underlying_price_used=bar.underlying_price_used,
        local_price=bar.local_price,
        premium_discount=bar.premium_discount,
        underlying_market_date=bar.underlying_market_date,
        fx_market_date=bar.fx_market_date,
        source=bar.source,
        source_ref=bar.source_ref,
        created_at=bar.created_at.isoformat(),
    )


@router.get(
    "/{symbol}/theoretical-price",
    response_model=TheoreticalPriceListResponse,
    summary="Get theoretical CEDEAR price history",
)
def get_theoretical_price_history(
    session: SessionDep,
    symbol: Annotated[str, Path(min_length=1, max_length=32, description="BYMA ticker")],
    start: Annotated[date | None, Query(description="Start market date (inclusive).")] = None,
    end: Annotated[date | None, Query(description="End market date (inclusive).")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TheoreticalPriceListResponse:
    """Return the theoretical price history for one CEDEAR.

    Every bar stores the three inputs (ratio, FX, underlying price) so the
    result can be independently verified. Premium/discount is computed from
    the stored actual CEDEAR close.

    Raises:
        HTTPException: ``404`` when the symbol is unknown.
    """
    normalised = symbol.strip().upper()
    instrument = session.scalar(select(Instrument).where(Instrument.symbol == normalised))
    if instrument is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "instrument_not_found",
                "symbol": normalised,
                "message": f"{normalised} is not in the stored CEDEAR universe.",
            },
        )

    filters = [TheoreticalPriceBar.instrument_id == instrument.id]
    if start is not None:
        filters.append(TheoreticalPriceBar.market_date >= start)
    if end is not None:
        filters.append(TheoreticalPriceBar.market_date <= end)

    total = (
        session.scalar(select(func.count()).select_from(TheoreticalPriceBar).where(*filters)) or 0
    )

    bars = session.scalars(
        select(TheoreticalPriceBar)
        .where(*filters)
        .order_by(TheoreticalPriceBar.market_date.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return TheoreticalPriceListResponse(
        items=[_to_theoretical_response(bar) for bar in bars],
        total=total,
        limit=limit,
        offset=offset,
    )


def _resolve_instrument(session: Session, symbol: str) -> Instrument:
    """Load an instrument by BYMA ticker or raise 404 (shared helper)."""
    return resolve_instrument(session, symbol)


@router.get(
    "/{symbol}/features",
    response_model=FeatureSnapshotResponse,
    summary="Get live feature snapshot",
)
def get_feature_snapshot(
    session: SessionDep,
    symbol: Annotated[str, Path(min_length=1, max_length=32, description="BYMA ticker")],
    as_of: Annotated[
        str | None,
        Query(
            description="Prediction instant (aware ISO-8601). Defaults to now; "
            "features only use bars strictly eligible at that instant."
        ),
    ] = None,
) -> FeatureSnapshotResponse:
    """Compute the full feature vector for one CEDEAR at a prediction instant.

    Read-only: the snapshot is computed from stored bars and returned with its
    ``as_of`` and ``feature_version``. Nothing is persisted here; persistence
    is the ``feature.build`` worker task's job. ``null`` means the history is
    too short for that feature, never a fabricated value.
    """
    from datetime import datetime

    instrument = _resolve_instrument(session, symbol)
    instant = ensure_utc(datetime.fromisoformat(as_of)) if as_of is not None else utc_now()
    values = compute_all_features(session, instrument.id, instant)
    serialised = {k: (float(v) if v is not None else None) for k, v in values.items()}
    return FeatureSnapshotResponse(
        symbol=instrument.symbol,
        instrument_id=instrument.id,
        as_of=instant.isoformat(),
        market_date=to_market_date(instant),
        feature_version=feature_version(),
        features=serialised,
    )


@router.get(
    "/{symbol}/features/snapshots",
    response_model=FeatureSnapshotListResponse,
    summary="List stored feature snapshots",
)
def list_stored_feature_snapshots(
    session: SessionDep,
    symbol: Annotated[str, Path(min_length=1, max_length=32, description="BYMA ticker")],
    version: Annotated[str | None, Query(description="Filter by feature version.")] = None,
    start: Annotated[date | None, Query(description="Start market date (inclusive).")] = None,
    end: Annotated[date | None, Query(description="End market date (inclusive).")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FeatureSnapshotListResponse:
    """Return persisted snapshots for one CEDEAR, newest first."""
    instrument = _resolve_instrument(session, symbol)
    total = count_feature_snapshots(
        session,
        instrument_id=instrument.id,
        feature_version=version,
        start=start,
        end=end,
    )
    rows: list[FeatureStore] = list(
        list_feature_snapshots(
            session,
            instrument_id=instrument.id,
            feature_version=version,
            start=start,
            end=end,
            limit=limit,
            offset=offset,
        )
    )
    return FeatureSnapshotListResponse(
        items=[
            StoredFeatureSnapshotResponse(
                as_of=row.as_of.isoformat(),
                market_date=row.market_date,
                feature_version=row.feature_version,
                features={
                    k: (float(v) if isinstance(v, int | float) else None)
                    for k, v in decode_feature_values(row).items()
                },
                created_at=row.created_at.isoformat() if row.created_at else "",
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


__all__ = [
    "CedearDetailResponse",
    "CedearListResponse",
    "CedearSummary",
    "FeatureSnapshotListResponse",
    "FeatureSnapshotResponse",
    "RatioPeriod",
    "StoredFeatureSnapshotResponse",
    "TheoreticalPriceBarResponse",
    "TheoreticalPriceListResponse",
    "router",
]
