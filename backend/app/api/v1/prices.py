"""Stored price-history read API.

Three endpoints, all read-only projections of what ingestion stored:

* ``GET /api/v1/cedears/{symbol}/history`` - BYMA CEDEAR bars in ARS.
* ``GET /api/v1/cedears/{symbol}/underlying`` - underlying bars in their own
  market and currency, with their own market dates.
* ``GET /api/v1/cedears/{symbol}/fx`` - the FX reference series (global, not
  per-instrument; the symbol only selects the universe row, so an unknown
  ticker 404s instead of returning a series for nothing).

``null`` means "the vendor did not publish it", everywhere: no-trade slots
are absent rows, never zeroes, and missing OHLCV fields are never defaulted.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import SessionDep, SettingsDep
from app.api.v1.common import MAX_PAGE_SIZE, resolve_instrument
from app.models.market_data import FxRate, LocalPriceBar, UnderlyingPriceBar

router = APIRouter(prefix="/cedears", tags=["prices"])


class LocalBarResponse(BaseModel):
    """One stored BYMA CEDEAR session in ARS."""

    symbol: str
    market_date: date
    timestamp: str = Field(description="Aware UTC ISO-8601 instant of the observation")
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    adjusted_close: Decimal | None = None
    volume: int | None = None
    traded_value: Decimal | None = None
    trades: int | None = None
    currency: str
    source: str
    source_ref: str | None = None


class LocalHistoryResponse(BaseModel):
    """A page of stored CEDEAR bars, newest first."""

    items: list[LocalBarResponse]
    total: int = Field(description="Total bars matching the filter, ignoring pagination")
    limit: int
    offset: int


class UnderlyingBarResponse(BaseModel):
    """One stored underlying session in its own market and currency."""

    symbol: str = Field(description="Underlying ticker as stored on the bar")
    market_date: date = Field(description="The underlying market's own trading date")
    timestamp: str = Field(description="Aware UTC ISO-8601 instant of the observation")
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    adjusted_close: Decimal | None = None
    volume: int | None = None
    traded_value: Decimal | None = None
    trades: int | None = None
    currency: str
    source: str
    source_ref: str | None = None


class UnderlyingHistoryResponse(BaseModel):
    """A page of stored underlying bars, newest first."""

    items: list[UnderlyingBarResponse]
    total: int = Field(description="Total bars matching the filter, ignoring pagination")
    limit: int
    offset: int


class FxBarResponse(BaseModel):
    """One stored FX observation (ARS per unit of the pair's base)."""

    pair: str
    market_date: date
    timestamp: str = Field(description="Aware UTC ISO-8601 instant of the observation")
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    currency: str
    source: str
    source_ref: str | None = None
    volume: int | None = None


class FxHistoryResponse(BaseModel):
    """A page of stored FX observations, newest first."""

    items: list[FxBarResponse]
    total: int = Field(description="Total observations matching the filter, ignoring pagination")
    limit: int
    offset: int


def _to_local_response(bar: LocalPriceBar) -> LocalBarResponse:
    """Serialise one CEDEAR bar without touching its nulls."""
    return LocalBarResponse(
        symbol=bar.symbol,
        market_date=bar.market_date,
        timestamp=bar.timestamp.isoformat(),
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        adjusted_close=bar.adjusted_close,
        volume=bar.volume,
        traded_value=bar.traded_value,
        trades=bar.trades,
        currency=bar.currency,
        source=bar.source,
        source_ref=bar.source_ref,
    )


def _to_underlying_response(bar: UnderlyingPriceBar) -> UnderlyingBarResponse:
    """Serialise one underlying bar without touching its nulls."""
    return UnderlyingBarResponse(
        symbol=bar.symbol,
        market_date=bar.market_date,
        timestamp=bar.timestamp.isoformat(),
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        adjusted_close=bar.adjusted_close,
        volume=bar.volume,
        traded_value=bar.traded_value,
        trades=bar.trades,
        currency=bar.currency,
        source=bar.source,
        source_ref=bar.source_ref,
    )


def _to_fx_response(bar: FxRate) -> FxBarResponse:
    """Serialise one FX observation without touching its nulls."""
    return FxBarResponse(
        pair=bar.pair,
        market_date=bar.market_date,
        timestamp=bar.timestamp.isoformat(),
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        currency=bar.currency,
        source=bar.source,
        source_ref=bar.source_ref,
        volume=bar.volume,
    )


@router.get(
    "/{symbol}/history", response_model=LocalHistoryResponse, summary="Get CEDEAR price history"
)
def get_local_history(
    session: SessionDep,
    symbol: str,
    start: Annotated[date | None, Query(description="Start market date (inclusive).")] = None,
    end: Annotated[date | None, Query(description="End market date (inclusive).")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LocalHistoryResponse:
    """Return stored BYMA bars for one CEDEAR, newest first."""
    instrument = resolve_instrument(session, symbol)
    filters: list[Any] = [LocalPriceBar.instrument_id == instrument.id]
    if start is not None:
        filters.append(LocalPriceBar.market_date >= start)
    if end is not None:
        filters.append(LocalPriceBar.market_date <= end)
    total = session.scalar(select(func.count()).select_from(LocalPriceBar).where(*filters)) or 0
    bars = session.scalars(
        select(LocalPriceBar)
        .where(*filters)
        .order_by(LocalPriceBar.market_date.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return LocalHistoryResponse(
        items=[_to_local_response(bar) for bar in bars], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{symbol}/underlying",
    response_model=UnderlyingHistoryResponse,
    summary="Get underlying price history",
)
def get_underlying_history(
    session: SessionDep,
    symbol: str,
    start: Annotated[date | None, Query(description="Start market date (inclusive).")] = None,
    end: Annotated[date | None, Query(description="End market date (inclusive).")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UnderlyingHistoryResponse:
    """Return stored underlying bars for one CEDEAR, newest first."""
    instrument = resolve_instrument(session, symbol)
    filters: list[Any] = [UnderlyingPriceBar.instrument_id == instrument.id]
    if start is not None:
        filters.append(UnderlyingPriceBar.market_date >= start)
    if end is not None:
        filters.append(UnderlyingPriceBar.market_date <= end)
    total = (
        session.scalar(select(func.count()).select_from(UnderlyingPriceBar).where(*filters)) or 0
    )
    bars = session.scalars(
        select(UnderlyingPriceBar)
        .where(*filters)
        .order_by(UnderlyingPriceBar.market_date.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return UnderlyingHistoryResponse(
        items=[_to_underlying_response(bar) for bar in bars],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{symbol}/fx", response_model=FxHistoryResponse, summary="Get FX reference history")
def get_fx_history(
    session: SessionDep,
    settings: SettingsDep,
    symbol: str,
    pair: Annotated[
        str | None, Query(description="Currency pair. Defaults to the ingestion pair.")
    ] = None,
    start: Annotated[date | None, Query(description="Start market date (inclusive).")] = None,
    end: Annotated[date | None, Query(description="End market date (inclusive).")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FxHistoryResponse:
    """Return the stored FX reference series (global; the ticker only validates)."""
    resolve_instrument(session, symbol)
    wanted = (pair or settings.fx_default_pair).strip().upper()
    filters: list[Any] = [FxRate.pair == wanted]
    if start is not None:
        filters.append(FxRate.market_date >= start)
    if end is not None:
        filters.append(FxRate.market_date <= end)
    total = session.scalar(select(func.count()).select_from(FxRate).where(*filters)) or 0
    bars = session.scalars(
        select(FxRate)
        .where(*filters)
        .order_by(FxRate.market_date.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return FxHistoryResponse(
        items=[_to_fx_response(bar) for bar in bars], total=total, limit=limit, offset=offset
    )


__all__ = [
    "FxBarResponse",
    "FxHistoryResponse",
    "LocalBarResponse",
    "LocalHistoryResponse",
    "UnderlyingBarResponse",
    "UnderlyingHistoryResponse",
    "router",
]
