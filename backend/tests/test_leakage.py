"""Leakage tests: no feature may see data published after its `as_of`.

These run against the migrated PostgreSQL schema (skipped cleanly when it is
unreachable) and encode the Phase 5 exit criterion: every feature is a pure
function of history strictly eligible at the prediction instant.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.features.cedear_technical import compute_cedear_technical_features
from app.features.core import get_feature_registry
from app.features.fx_features import compute_fx_features
from app.features.relative_features import compute_relative_features
from app.models.instrument import Instrument
from app.models.market_data import FxRate, LocalPriceBar, UnderlyingPriceBar

pytestmark = pytest.mark.leakage

D = date(2026, 3, 10)


def _as_of(day: date = D) -> datetime:
    """BYMA-close prediction instant for ``day`` (18:00 ART == 21:00 UTC)."""
    return datetime(day.year, day.month, day.day, 21, 0, tzinfo=UTC)


def _utc_noon(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)


def _instrument(session: Session, symbol: str = "LEAK") -> Instrument:
    inst = Instrument(symbol=symbol, instrument_type="STOCK")
    session.add(inst)
    session.flush()
    return inst


def _local(
    session: Session,
    inst: Instrument,
    day: date,
    close: str,
    symbol: str = "LEAK",
) -> None:
    session.add(
        LocalPriceBar(
            instrument_id=inst.id,
            symbol=symbol,
            market_date=day,
            timestamp=_utc_noon(day),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=1000,
            currency="ARS",
            source="test",
        )
    )


def _underlying(session: Session, inst: Instrument, day: date, close: str) -> None:
    session.add(
        UnderlyingPriceBar(
            instrument_id=inst.id,
            symbol="LEAK-U",
            market_date=day,
            timestamp=_utc_noon(day),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=1000,
            currency="USD",
            source="test",
        )
    )


def _fx(session: Session, day: date, close: str) -> None:
    session.add(
        FxRate(
            pair="USDARS",
            market_date=day,
            timestamp=_utc_noon(day),
            close=Decimal(close),
            currency="ARS",
            source="test",
        )
    )


class TestUnderlyingLag:
    """The one-session underlying lag is enforced at feature time."""

    def test_same_day_underlying_close_is_invisible(self, db_session: Session) -> None:
        """The underlying close of day D must not reach a prediction at D's close."""
        inst = _instrument(db_session)
        d_2 = date(2026, 3, 6)
        d_1 = date(2026, 3, 9)
        _underlying(db_session, inst, d_2, "100")
        _underlying(db_session, inst, d_1, "110")  # +10%
        _underlying(db_session, inst, D, "200")  # same-day spike: must be ignored
        _fx(db_session, d_1, "1000")
        _fx(db_session, D, "1000")  # flat FX
        db_session.commit()

        result = compute_relative_features(get_feature_registry(), inst.id, _as_of(), db_session)
        # Compounded from lagged sessions only: (1.10) * (1.00) - 1 == 0.10.
        assert result["expected_cedear_return"] == pytest.approx(0.10)

    def test_no_underlying_history_means_no_expectation(self, db_session: Session) -> None:
        """A lone same-day bar is not history; the expectation stays null."""
        inst = _instrument(db_session, symbol="LEAK2")
        _underlying(db_session, inst, D, "200")  # only the forbidden same-day bar
        _fx(db_session, D, "1000")
        db_session.commit()

        result = compute_relative_features(get_feature_registry(), inst.id, _as_of(), db_session)
        assert result["expected_cedear_return"] is None


class TestLocalAsOf:
    """Technical windows end strictly before the prediction date."""

    def test_bar_on_prediction_date_does_not_move_technicals(self, db_session: Session) -> None:
        """An extreme print on D leaves the pre-D technical window untouched."""
        inst = _instrument(db_session, symbol="LEAK3")
        days = [date(2026, 3, d) for d in range(2, 10)]  # 8 sessions before D
        for i, day in enumerate(days):
            _local(db_session, inst, day, str(100 + i))
        _local(db_session, inst, D, "99999")  # extreme print on D: must be ignored
        db_session.commit()

        result = compute_cedear_technical_features(
            get_feature_registry(), inst.id, _as_of(), db_session
        )
        # 1d return over the last two *eligible* closes: 107 vs 106.
        assert result["cedear_return_1d"] == pytest.approx((107 - 106) / 106)


class TestFxAsOf:
    """FX has no lag, but the future is still the future."""

    def test_future_fx_is_invisible(self, db_session: Session) -> None:
        """An FX observation dated after D cannot move features at D."""
        inst = _instrument(db_session, symbol="LEAK4")
        # FX windows end at D: two eligible bars give a return, the future one is ignored.
        _fx(db_session, date(2026, 3, 8), "1000")
        _fx(db_session, date(2026, 3, 9), "1100")  # +10% on the last eligible bar
        _fx(db_session, date(2026, 3, 11), "5000")  # future: must be ignored
        db_session.commit()

        result = compute_fx_features(get_feature_registry(), inst.id, _as_of(), db_session)
        assert result["fx_return_1d"] == pytest.approx(0.10)


class TestNaiveDatetimesRejected:
    """Naive datetimes are rejected before any market date is derived."""

    def test_naive_as_of_raises(self, db_session: Session) -> None:
        """A naive prediction instant raises instead of guessing a timezone."""
        inst = _instrument(db_session, symbol="LEAK5")
        db_session.commit()
        with pytest.raises(ValueError, match="Naive datetime"):
            compute_cedear_technical_features(
                get_feature_registry(), inst.id, datetime(2026, 3, 10, 21, 0), db_session
            )
