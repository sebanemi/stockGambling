"""Theoretical CEDEAR price computation.

The theoretical price is an accounting identity:
    theoretical = underlying_price * fx / ratio

This module implements the computation using the as-of rule from
:mod:`app.alignment.asof` to ensure no look-ahead leakage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alignment.asof import DEFAULT_UNDERLYING_LAG, select_fx_observation, select_underlying_bar
from app.core.time import to_market_date
from app.domain.vocabulary import UnderlyingMarket
from app.models.instrument import Instrument, InstrumentRatioHistory
from app.models.market_data import FxRate, LocalPriceBar, UnderlyingPriceBar
from app.providers.base import DailyBar, FxBar

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo


def _to_daily_bar(row: UnderlyingPriceBar) -> DailyBar:
    """Project an underlying ORM row onto the as-of selector's record type."""
    return DailyBar(
        symbol=row.symbol,
        market_date=row.market_date,
        timestamp=row.timestamp,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        adjusted_close=row.adjusted_close,
        volume=row.volume,
        traded_value=None,
        trades=None,
        currency=row.currency,
        source=row.source,
    )


def _to_fx_bar(row: FxRate) -> FxBar:
    """Project an FX ORM row onto the as-of selector's record type."""
    return FxBar(
        pair=row.pair,
        market_date=row.market_date,
        timestamp=row.timestamp,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        currency=row.currency,
        source=row.source,
        volume=row.volume,
    )


@dataclass(frozen=True, slots=True)
class TheoreticalInputs:
    """The three inputs needed to compute a theoretical price.

    All three are resolved as-of the BYMA close instant for the
    given market date.
    """

    ratio: Decimal
    underlying_bar: DailyBar
    fx_bar: FxBar


@dataclass(frozen=True, slots=True)
class TheoreticalResult:
    """The computed theoretical price with full provenance."""

    theoretical_price: Decimal
    ratio_used: Decimal
    fx_used: Decimal
    underlying_price_used: Decimal
    underlying_market_date: date
    fx_market_date: date
    local_price: Decimal | None
    premium_discount: Decimal | None


def _get_ratio_for_date(instrument: Instrument, market_date: date) -> InstrumentRatioHistory | None:
    """Return the ratio row in force on ``market_date``."""
    return instrument.ratio_effective_on(market_date)


def _get_local_price(
    session: Session, instrument_id: int, market_date: date
) -> LocalPriceBar | None:
    """Fetch the local CEDEAR close for the given date."""
    return session.scalar(
        select(LocalPriceBar).where(
            LocalPriceBar.instrument_id == instrument_id,
            LocalPriceBar.market_date == market_date,
        )
    )


def resolve_theoretical_inputs(
    session: Session,
    instrument: Instrument,
    prediction_instant: datetime,
    *,
    underlying_lag: int = DEFAULT_UNDERLYING_LAG,
) -> TheoreticalInputs | None:
    """Resolve the three inputs for a theoretical price at ``prediction_instant``.

    Args:
        session: Database session.
        instrument: The CEDEAR instrument.
        prediction_instant: The BYMA close instant (timezone-aware UTC).
        underlying_lag: How many completed underlying sessions to step back.

    Returns:
        A :class:`TheoreticalInputs` tuple, or ``None`` if any input is missing.
    """
    # The BYMA market date of the prediction.
    byma_date = to_market_date(prediction_instant)

    # 1. Ratio valid on the BYMA date.
    ratio_row = _get_ratio_for_date(instrument, byma_date)
    if ratio_row is None:
        return None

    # 2. Underlying bar selected by the as-of rule (with lag).
    underlying_rows = session.scalars(
        select(UnderlyingPriceBar).where(UnderlyingPriceBar.instrument_id == instrument.id)
    ).all()

    if not instrument.underlying_market:
        return None

    try:
        market_tz: ZoneInfo = UnderlyingMarket(instrument.underlying_market).tz
    except (ValueError, KeyError):
        return None

    underlying_bar = select_underlying_bar(
        [_to_daily_bar(row) for row in underlying_rows],
        prediction_instant,
        market_tz,
        lag_sessions=underlying_lag,
    )
    if underlying_bar is None:
        return None

    # 3. FX observation strictly before the prediction instant.
    fx_rows = session.scalars(select(FxRate).where(FxRate.pair == "USDARS")).all()
    fx_bar = select_fx_observation([_to_fx_bar(row) for row in fx_rows], prediction_instant)
    if fx_bar is None:
        return None

    # A bar without a close is unusable; treat it as a missing input.
    if underlying_bar.close is None or fx_bar.close is None:
        return None

    return TheoreticalInputs(
        ratio=ratio_row.ratio,
        underlying_bar=underlying_bar,
        fx_bar=fx_bar,
    )


def compute_theoretical_price(inputs: TheoreticalInputs) -> Decimal | None:
    """Compute the theoretical CEDEAR price.

    Formula: underlying_price * fx / ratio

    All values are Decimal for precision. Returns ``None`` when a selected
    bar carries no close (treated as a missing input upstream).
    """
    underlying_close = inputs.underlying_bar.close
    fx_close = inputs.fx_bar.close
    if underlying_close is None or fx_close is None:
        return None
    return underlying_close * fx_close / inputs.ratio


def compute_premium_discount(
    local_price: Decimal | None, theoretical_price: Decimal
) -> Decimal | None:
    """Compute premium/discount as (local - theoretical) / theoretical.

    Returns ``None`` when the local price is not available.
    """
    if local_price is None:
        return None
    return (local_price - theoretical_price) / theoretical_price


def build_theoretical_result(
    session: Session,
    instrument: Instrument,
    prediction_instant: datetime,
    *,
    underlying_lag: int = DEFAULT_UNDERLYING_LAG,
) -> TheoreticalResult | None:
    """Build a complete theoretical result for one instrument at one instant.

    This is the main entry point used by the ingestion task.
    """
    inputs = resolve_theoretical_inputs(
        session,
        instrument,
        prediction_instant,
        underlying_lag=underlying_lag,
    )
    if inputs is None:
        return None

    theoretical = compute_theoretical_price(inputs)
    if theoretical is None:
        return None

    local_bar = _get_local_price(session, instrument.id, to_market_date(prediction_instant))
    local_price = local_bar.close if local_bar else None
    premium_discount = compute_premium_discount(local_price, theoretical)

    fx_used = inputs.fx_bar.close
    underlying_used = inputs.underlying_bar.close
    if fx_used is None or underlying_used is None:
        return None

    return TheoreticalResult(
        theoretical_price=theoretical.quantize(Decimal("0.000001")),
        ratio_used=inputs.ratio,
        fx_used=fx_used.quantize(Decimal("0.000001")),
        underlying_price_used=underlying_used.quantize(Decimal("0.000001")),
        underlying_market_date=inputs.underlying_bar.market_date,
        fx_market_date=inputs.fx_bar.market_date,
        local_price=local_price.quantize(Decimal("0.000001")) if local_price is not None else None,
        premium_discount=(
            premium_discount.quantize(Decimal("0.000001")) if premium_discount is not None else None
        ),
    )


__all__ = [
    "TheoreticalInputs",
    "TheoreticalResult",
    "build_theoretical_result",
    "compute_premium_discount",
    "compute_theoretical_price",
    "resolve_theoretical_inputs",
]
