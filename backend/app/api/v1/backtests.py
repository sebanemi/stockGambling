"""Backtest read/write API.

* ``POST /api/v1/backtests`` - simulate a long/flat CEDEAR strategy over
  **stored** local bars and persist the run.
* ``GET /api/v1/backtests`` - persisted runs, newest first.
* ``GET /api/v1/backtests/{backtest_id}`` - one persisted run, exactly as
  stored (nothing is recomputed at read time).

The simulation runs synchronously: it is O(N) numpy over a bounded window
(``MAX_BACKTEST_BARS``), while model training and walk-forward evaluation
stay in the worker. Signals are explicit client input - one binary target
per holding-period epoch - and the prices always come from the database, so
every number in the response traces to stored bars plus the posted signals.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import SessionDep
from app.api.v1.common import MAX_PAGE_SIZE, resolve_instrument
from app.backtesting.costs import (
    DEFAULT_COMMISSION_RATE,
    DEFAULT_SLIPPAGE_RATE,
    BacktestCosts,
)
from app.backtesting.store import save_backtest
from app.models.backtest import Backtest
from app.models.market_data import LocalPriceBar

router = APIRouter(prefix="/backtests", tags=["backtests"])

#: Synchronous-execution bound: wider windows belong in the worker.
MAX_BACKTEST_BARS = 5000


class BacktestRequest(BaseModel):
    """Input for one backtest simulation."""

    symbol: str = Field(description="BYMA ticker; prices come from stored bars.")
    start: date = Field(description="Start market date (inclusive).")
    end: date = Field(description="End market date (inclusive).")
    signals: list[Literal[0, 1]] = Field(
        description="Binary position targets, one per holding-period epoch."
    )
    holding_period: int = Field(
        default=1,
        ge=1,
        le=504,
        description="Sessions each signal is held; must divide the bar intervals exactly.",
    )
    initial_capital: float = Field(default=1_000_000.0, gt=0.0, description="Starting cash in ARS.")
    position_fraction: float = Field(
        default=1.0, gt=0.0, le=1.0, description="Cash fraction deployed on each entry."
    )
    commission_rate: float = Field(
        default=DEFAULT_COMMISSION_RATE, ge=0.0, lt=1.0, description="Per-fill commission."
    )
    slippage_rate: float = Field(
        default=DEFAULT_SLIPPAGE_RATE, ge=0.0, lt=1.0, description="Per-fill slippage."
    )


class BacktestSummary(BaseModel):
    """One persisted run without its vectors."""

    id: int
    symbol: str
    start_date: date
    end_date: date
    n_bars: int
    holding_period: int = Field(description="Sessions each signal was held")
    total_costs: Decimal
    metrics: dict[str, Any]
    created_at: str


class BacktestListResponse(BaseModel):
    """A page of persisted runs, newest first."""

    items: list[BacktestSummary]
    total: int = Field(description="Total runs, ignoring pagination")
    limit: int
    offset: int


class BacktestResponse(BacktestSummary):
    """One persisted run with its full vectors, exactly as simulated."""

    params: dict[str, Any]
    signals: list[int]
    trades: list[dict[str, Any]]
    equity_curve: list[float]
    benchmark_buy_hold: list[float]
    benchmark_metrics: dict[str, Any]
    status: str
    notes: str | None = None


def _to_summary(row: Backtest) -> BacktestSummary:
    """Serialise a persisted run without its vectors."""
    holding = row.params.get("holding_period", 1)
    return BacktestSummary(
        id=row.id,
        symbol=row.instrument.symbol,
        start_date=row.start_date,
        end_date=row.end_date,
        n_bars=row.n_bars,
        holding_period=int(holding) if isinstance(holding, int) else 1,
        total_costs=row.total_costs,
        metrics=row.metrics,
        created_at=row.created_at.isoformat(),
    )


def _to_response(row: Backtest) -> BacktestResponse:
    """Serialise a persisted run with its full vectors."""
    return BacktestResponse(
        **_to_summary(row).model_dump(),
        params=row.params,
        signals=row.signals,
        trades=row.trades,
        equity_curve=row.equity_curve,
        benchmark_buy_hold=row.benchmark_buy_hold,
        benchmark_metrics=row.benchmark_metrics,
        status=row.status,
        notes=row.notes,
    )


@router.post("", response_model=BacktestResponse, status_code=status.HTTP_201_CREATED)
@router.post(
    "/",
    response_model=BacktestResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def create_backtest(session: SessionDep, payload: BacktestRequest) -> BacktestResponse:
    """Simulate the strategy over stored bars and persist the run.

    Raises:
        HTTPException: ``404`` for an unknown symbol, ``422`` when the
            window is inverted, holds fewer than two stored bars, is wider
            than the synchronous bound, or the signal count does not match
            the holding-period epochs.
    """
    if payload.start > payload.end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "invalid_window",
                "message": "start must not be after end.",
            },
        )
    instrument = resolve_instrument(session, payload.symbol)
    bars = session.scalars(
        select(LocalPriceBar)
        .where(
            LocalPriceBar.instrument_id == instrument.id,
            LocalPriceBar.market_date >= payload.start,
            LocalPriceBar.market_date <= payload.end,
        )
        .order_by(LocalPriceBar.market_date)
    ).all()
    closes: list[float] = []
    for bar in bars:
        if bar.close is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "insufficient_history",
                    "symbol": instrument.symbol,
                    "message": (
                        "A stored bar in the window has no close; "
                        "a backtest needs closes on every bar."
                    ),
                },
            )
        closes.append(float(bar.close))
    if len(closes) > MAX_BACKTEST_BARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "range_too_wide",
                "message": (
                    f"{len(closes)} stored bars exceed the synchronous bound of "
                    f"{MAX_BACKTEST_BARS}; run wide backtests in the worker."
                ),
            },
        )
    if len(closes) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "insufficient_history",
                "symbol": instrument.symbol,
                "message": (
                    "The window holds fewer than two stored bars with closes; "
                    "a backtest needs at least one price interval."
                ),
            },
        )
    if len(payload.signals) * payload.holding_period != len(closes) - 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "signal_length_mismatch",
                "message": (
                    f"Need one signal per {payload.holding_period}-session epoch: got "
                    f"{len(payload.signals)} signals for {len(closes)} stored bars."
                ),
            },
        )
    prices = [float(close) for close in closes]
    row = save_backtest(
        session,
        instrument_id=instrument.id,
        start=payload.start,
        end=payload.end,
        closes=prices,
        signals=[int(v) for v in payload.signals],
        holding_period=payload.holding_period,
        initial_capital=payload.initial_capital,
        costs=BacktestCosts(
            commission_rate=payload.commission_rate, slippage_rate=payload.slippage_rate
        ),
        position_fraction=payload.position_fraction,
    )
    return _to_response(row)


@router.get("", response_model=BacktestListResponse, summary="List backtests")
@router.get(
    "/",
    response_model=BacktestListResponse,
    include_in_schema=False,
    summary="List backtests (trailing slash)",
)
def list_backtests(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BacktestListResponse:
    """Return persisted runs, newest first."""
    total = session.scalar(select(func.count()).select_from(Backtest)) or 0
    rows = session.scalars(
        select(Backtest)
        .options(selectinload(Backtest.instrument))
        .order_by(Backtest.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return BacktestListResponse(
        items=[_to_summary(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{backtest_id}", response_model=BacktestResponse, summary="Get one backtest")
def get_backtest(session: SessionDep, backtest_id: int) -> BacktestResponse:
    """Return one persisted run, exactly as simulated.

    Raises:
        HTTPException: ``404`` when the id is unknown.
    """
    row = session.scalar(select(Backtest).where(Backtest.id == backtest_id))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "backtest_not_found",
                "backtest_id": backtest_id,
                "message": f"Backtest id {backtest_id} does not exist.",
            },
        )
    return _to_response(row)


__all__ = [
    "MAX_BACKTEST_BARS",
    "BacktestListResponse",
    "BacktestRequest",
    "BacktestResponse",
    "BacktestSummary",
    "router",
]
