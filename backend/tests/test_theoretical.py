"""Theoretical CEDEAR price engine tests.

They prove the Phase 4 invariants: the formula is applied correctly,
historical ratios are honoured, all inputs are stored for auditability,
and recomputation after a ratio change affects only the changed dates.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.vocabulary import UnderlyingMarket
from app.ingestion.theoretical import (
    refresh_instrument_theoretical,
    run_theoretical_ingestion,
)
from app.models.instrument import IngestionRun, Instrument
from app.models.market_data import FxRate, LocalPriceBar, UnderlyingPriceBar
from app.models.theoretical import TheoreticalPriceBar
from app.providers.base import DailyBar, FxBar
from app.theoretical.engine import (
    TheoreticalInputs,
    build_theoretical_result,
    compute_premium_discount,
    compute_theoretical_price,
)

pytestmark = pytest.mark.integration

START = date(2026, 7, 13)
END = date(2026, 7, 14)

#: The underlying session usable by a prediction at the BYMA close of START:
#: the one-session lag means the latest eligible underlying date is strictly
#: before the prediction's own date.
PREV = date(2026, 7, 12)

# BYMA close: 18:00 ART = 21:00 UTC
BYMA_CLOSE_INSTANT = datetime(2026, 7, 13, 21, 0, tzinfo=UTC)

# Underlying (NASDAQ) close: 16:00 ET = 20:00 UTC (summer) / 21:00 UTC (winter)
# For July, NASDAQ is on EDT (UTC-4), so 16:00 ET = 20:00 UTC
NASDAQ_CLOSE_INSTANT = datetime(2026, 7, 13, 20, 0, tzinfo=UTC)
PREV_NASDAQ_CLOSE_INSTANT = datetime(2026, 7, 12, 20, 0, tzinfo=UTC)

# FX observation before BYMA close
FX_INSTANT = datetime(2026, 7, 13, 19, 30, tzinfo=UTC)


def _local_bar(
    session: Session, instrument_id: int, market_date: date, *, close: str
) -> LocalPriceBar:
    """Create a local CEDEAR bar."""
    bar = LocalPriceBar(
        instrument_id=instrument_id,
        symbol="AAPL",
        market_date=market_date,
        timestamp=BYMA_CLOSE_INSTANT,
        open=Decimal("200"),
        high=Decimal("210"),
        low=Decimal("195"),
        close=Decimal(close),
        adjusted_close=Decimal(close),
        volume=100000,
        traded_value=Decimal("20000000"),
        trades=500,
        currency="ARS",
        source="yahoo",
    )
    session.add(bar)
    session.flush()
    return bar


def _underlying_bar(
    session: Session,
    instrument_id: int,
    market_date: date,
    *,
    close: str,
    timestamp: datetime = NASDAQ_CLOSE_INSTANT,
) -> UnderlyingPriceBar:
    """Create an underlying price bar."""
    bar = UnderlyingPriceBar(
        instrument_id=instrument_id,
        symbol="AAPL",
        market_date=market_date,
        timestamp=timestamp,
        open=Decimal("200"),
        high=Decimal("210"),
        low=Decimal("195"),
        close=Decimal(close),
        adjusted_close=Decimal(close),
        volume=50000000,
        currency="USD",
        source="yahoo",
    )
    session.add(bar)
    session.flush()
    return bar


def _fx_bar(session: Session, market_date: date, *, close: Decimal) -> FxRate:
    """Create an FX rate observation."""
    bar = FxRate(
        pair="USDARS",
        market_date=market_date,
        timestamp=FX_INSTANT,
        open=close - Decimal("1"),
        high=close + Decimal("1"),
        low=close - Decimal("2"),
        close=close,
        currency="ARS",
        source="yahoo",
    )
    session.add(bar)
    session.flush()
    return bar


def _instrument(session: Session, symbol: str = "AAPL") -> Instrument:
    """Create an instrument with a known ratio."""
    from app.models.instrument import InstrumentRatioHistory

    instrument = Instrument(
        symbol=symbol,
        name=f"{symbol} Inc",
        underlying_symbol=f"{symbol}.US",
        underlying_market=UnderlyingMarket.NASDAQ_GS.value,
        underlying_market_raw="NASDAQ_GS",
    )
    session.add(instrument)
    session.flush()

    # Add a ratio period covering the test dates
    ratio = InstrumentRatioHistory(
        instrument_id=instrument.id,
        ratio=Decimal("10"),  # 10:1
        effective_from=date(2026, 1, 1),
        effective_to=None,
        source="test",
        source_ref="test",
    )
    session.add(ratio)
    session.flush()

    return instrument


class TestComputeTheoreticalPrice:
    """The core formula: theoretical = underlying * fx / ratio."""

    def test_basic_formula(self) -> None:
        """10 * 1000 / 10 = 1000."""
        inputs = TheoreticalInputs(
            ratio=Decimal("10"),
            underlying_bar=DailyBar(
                symbol="AAPL",
                market_date=START,
                timestamp=NASDAQ_CLOSE_INSTANT,
                close=Decimal("1000"),
                source="test",
            ),
            fx_bar=FxBar(
                pair="USDARS",
                market_date=START,
                timestamp=FX_INSTANT,
                close=Decimal("1000"),
                currency="ARS",
                source="test",
            ),
        )
        result = compute_theoretical_price(inputs)
        assert result == Decimal("100000")  # 1000 * 1000 / 10

    def test_ratio_one_to_five(self) -> None:
        """Ratio 0.2 (1:5) means 5 underlying per CEDEAR."""
        inputs = TheoreticalInputs(
            ratio=Decimal("0.2"),
            underlying_bar=DailyBar(
                symbol="AAPL",
                market_date=START,
                timestamp=NASDAQ_CLOSE_INSTANT,
                close=Decimal("100"),
                source="test",
            ),
            fx_bar=FxBar(
                pair="USDARS",
                market_date=START,
                timestamp=FX_INSTANT,
                close=Decimal("1000"),
                currency="ARS",
                source="test",
            ),
        )
        result = compute_theoretical_price(inputs)
        # 100 * 1000 / 0.2 = 100 * 1000 * 5 = 500000
        assert result == Decimal("500000")

    def test_premium_discount_positive(self) -> None:
        """Local price above theoretical = positive premium."""
        premium = compute_premium_discount(Decimal("1100"), Decimal("1000"))
        assert premium == Decimal("0.1")  # 10%

    def test_premium_discount_negative(self) -> None:
        """Local price below theoretical = negative discount."""
        discount = compute_premium_discount(Decimal("900"), Decimal("1000"))
        assert discount == Decimal("-0.1")  # -10%

    def test_premium_discount_none_when_missing_local(self) -> None:
        """No local price -> no premium/discount."""
        result = compute_premium_discount(None, Decimal("1000"))
        assert result is None


class TestBuildTheoreticalResult:
    """The end-to-end computation using stored bars and the as-of rule."""

    def test_happy_path(self, db_session: Session) -> None:
        """All three inputs present -> theoretical price computed."""
        instrument = _instrument(db_session)
        _local_bar(db_session, instrument.id, START, close="215000")
        # Lagged underlying session: strictly before the BYMA date.
        _underlying_bar(
            db_session,
            instrument.id,
            PREV,
            close="150",
            timestamp=PREV_NASDAQ_CLOSE_INSTANT,
        )
        _fx_bar(db_session, START, close=Decimal("1000"))

        result = build_theoretical_result(db_session, instrument, BYMA_CLOSE_INSTANT)
        assert result is not None
        # 150 * 1000 / 10 = 15000
        assert result.theoretical_price == Decimal("15000")
        assert result.ratio_used == Decimal("10")
        assert result.fx_used == Decimal("1000")
        assert result.underlying_price_used == Decimal("150")
        assert result.local_price == Decimal("215000")
        assert result.premium_discount == Decimal("13.333333")  # (215000-15000)/15000
        assert result.underlying_market_date == PREV

    def test_missing_ratio_returns_none(self, db_session: Session) -> None:
        """No ratio period covering the date -> None."""
        from sqlalchemy import delete

        from app.models.instrument import InstrumentRatioHistory

        instrument = _instrument(db_session)
        # Remove the ratio
        db_session.execute(
            delete(InstrumentRatioHistory).where(
                InstrumentRatioHistory.instrument_id == instrument.id
            )
        )

        _underlying_bar(
            db_session,
            instrument.id,
            PREV,
            close="150",
            timestamp=PREV_NASDAQ_CLOSE_INSTANT,
        )
        _fx_bar(db_session, START, close=Decimal("1000"))

        result = build_theoretical_result(db_session, instrument, BYMA_CLOSE_INSTANT)
        assert result is None

    def test_missing_underlying_bar_returns_none(self, db_session: Session) -> None:
        """No eligible underlying bar -> None."""
        instrument = _instrument(db_session)
        _local_bar(db_session, instrument.id, START, close="215000")
        _fx_bar(db_session, START, close=Decimal("1000"))
        # No underlying bar added

        result = build_theoretical_result(db_session, instrument, BYMA_CLOSE_INSTANT)
        assert result is None

    def test_missing_fx_returns_none(self, db_session: Session) -> None:
        """No FX observation -> None."""
        instrument = _instrument(db_session)
        _local_bar(db_session, instrument.id, START, close="215000")
        _underlying_bar(
            db_session,
            instrument.id,
            PREV,
            close="150",
            timestamp=PREV_NASDAQ_CLOSE_INSTANT,
        )
        # No FX bar added

        result = build_theoretical_result(db_session, instrument, BYMA_CLOSE_INSTANT)
        assert result is None

    def test_uses_historical_ratio_not_current(self, db_session: Session) -> None:
        """A ratio change mid-series affects only the affected dates."""
        instrument = _instrument(db_session)

        # Close the open [2026-01-01, None) period at END, then open [END, None).
        from app.models.instrument import InstrumentRatioHistory

        current = db_session.scalar(
            select(InstrumentRatioHistory).where(
                InstrumentRatioHistory.instrument_id == instrument.id,
                InstrumentRatioHistory.effective_to.is_(None),
            )
        )
        assert current is not None
        current.effective_to = END
        db_session.flush()

        # Add a second ratio period starting 2026-07-14 (10:1 -> 20:1)
        new_ratio = InstrumentRatioHistory(
            instrument_id=instrument.id,
            ratio=Decimal("20"),  # Changed to 20:1
            effective_from=END,  # 2026-07-14
            effective_to=None,
            source="test",
            source_ref="test",
        )
        db_session.add(new_ratio)
        db_session.flush()

        # Bars for both dates; underlying sessions lag one day behind each prediction.
        _local_bar(db_session, instrument.id, START, close="215000")
        _local_bar(db_session, instrument.id, END, close="220000")
        _underlying_bar(
            db_session, instrument.id, PREV, close="150", timestamp=PREV_NASDAQ_CLOSE_INSTANT
        )
        # Eligible underlying session for the END prediction (< 2026-07-14).
        _underlying_bar(db_session, instrument.id, START, close="150")
        _fx_bar(db_session, START, close=Decimal("1000"))
        _fx_bar(db_session, END, close=Decimal("1000"))

        # Theoretical price on START should use ratio 10
        result_start = build_theoretical_result(db_session, instrument, BYMA_CLOSE_INSTANT)
        assert result_start is not None
        assert result_start.ratio_used == Decimal("10")
        assert result_start.theoretical_price == Decimal("15000")

        # Theoretical price on END should use ratio 20
        end_instant = datetime(2026, 7, 14, 21, 0, tzinfo=UTC)
        result_end = build_theoretical_result(db_session, instrument, end_instant)
        assert result_end is not None
        assert result_end.ratio_used == Decimal("20")
        assert result_end.theoretical_price == Decimal("7500")  # 150 * 1000 / 20


class TestRunTheoreticalIngestion:
    """The full write path: compute + store, one audit row per run."""

    def test_computes_and_stores_theoretical_prices(self, db_session: Session) -> None:
        """Theoretical bars land in sg_theoretical_price_history."""
        instrument = _instrument(db_session)
        _local_bar(db_session, instrument.id, START, close="215000")
        _underlying_bar(
            db_session, instrument.id, PREV, close="150", timestamp=PREV_NASDAQ_CLOSE_INSTANT
        )
        _fx_bar(db_session, START, close=Decimal("1000"))

        report = run_theoretical_ingestion(
            db_session,
            [instrument],
            prediction_instant=BYMA_CLOSE_INSTANT,
        )

        assert report.status == "succeeded"
        assert report.theoretical_bars_inserted == 1
        assert report.instruments == ("AAPL",)

        stored = db_session.scalar(
            select(TheoreticalPriceBar).where(TheoreticalPriceBar.instrument_id == instrument.id)
        )
        assert stored is not None
        assert stored.market_date == START
        assert stored.theoretical_price == Decimal("15000")
        assert stored.ratio_used == Decimal("10")
        assert stored.fx_used == Decimal("1000")
        assert stored.underlying_price_used == Decimal("150")
        assert stored.local_price == Decimal("215000")
        assert stored.premium_discount == Decimal("13.333333")
        assert stored.underlying_market_date == PREV
        assert stored.fx_market_date == START
        assert stored.source == "computed"

    def test_the_run_row_records_what_was_computed(self, db_session: Session) -> None:
        """The audit row captures job and scope."""
        instrument = _instrument(db_session)
        _local_bar(db_session, instrument.id, START, close="215000")
        _underlying_bar(
            db_session, instrument.id, PREV, close="150", timestamp=PREV_NASDAQ_CLOSE_INSTANT
        )
        _fx_bar(db_session, START, close=Decimal("1000"))

        run_theoretical_ingestion(
            db_session,
            [instrument],
            prediction_instant=BYMA_CLOSE_INSTANT,
        )

        run = db_session.scalar(select(IngestionRun).order_by(IngestionRun.id.desc()))
        assert run is not None
        assert run.job == "ingest.theoretical_prices"
        assert run.status == "succeeded"
        assert run.sources == ["computed"]
        assert run.instruments_seen == 1

    def test_reingest_is_idempotent(self, db_session: Session) -> None:
        """Re-running identical inputs is a zero-count pass."""
        instrument = _instrument(db_session)
        _local_bar(db_session, instrument.id, START, close="215000")
        _underlying_bar(
            db_session, instrument.id, PREV, close="150", timestamp=PREV_NASDAQ_CLOSE_INSTANT
        )
        _fx_bar(db_session, START, close=Decimal("1000"))

        run_theoretical_ingestion(
            db_session,
            [instrument],
            prediction_instant=BYMA_CLOSE_INSTANT,
        )

        again = run_theoretical_ingestion(
            db_session,
            [instrument],
            prediction_instant=BYMA_CLOSE_INSTANT,
        )
        assert again.theoretical_bars_inserted == 0
        assert again.theoretical_bars_updated == 0

        count = db_session.scalar(
            select(func.count())
            .select_from(TheoreticalPriceBar)
            .where(TheoreticalPriceBar.instrument_id == instrument.id)
        )
        assert count == 1

    def test_revised_underlying_updates_in_place(self, db_session: Session) -> None:
        """A restated underlying close revises one row."""
        instrument = _instrument(db_session)
        _local_bar(db_session, instrument.id, START, close="215000")
        _underlying_bar(
            db_session, instrument.id, PREV, close="150", timestamp=PREV_NASDAQ_CLOSE_INSTANT
        )
        _fx_bar(db_session, START, close=Decimal("1000"))

        run_theoretical_ingestion(
            db_session,
            [instrument],
            prediction_instant=BYMA_CLOSE_INSTANT,
        )

        # Update underlying bar
        underlying = db_session.scalar(
            select(UnderlyingPriceBar).where(UnderlyingPriceBar.instrument_id == instrument.id)
        )
        assert underlying is not None
        underlying.close = Decimal("151")
        db_session.flush()

        revised = run_theoretical_ingestion(
            db_session,
            [instrument],
            prediction_instant=BYMA_CLOSE_INSTANT,
        )
        assert revised.theoretical_bars_updated == 1

        stored = db_session.scalar(
            select(TheoreticalPriceBar).where(TheoreticalPriceBar.instrument_id == instrument.id)
        )
        assert stored is not None
        # 151 * 1000 / 10 = 15100
        assert stored.theoretical_price == Decimal("15100")
        assert stored.underlying_price_used == Decimal("151")

    def test_missing_input_is_a_warning_not_a_failure(self, db_session: Session) -> None:
        """One instrument missing inputs doesn't fail the run."""
        good = _instrument(db_session, "AAPL")
        bad = _instrument(db_session, "GOLD")
        bad.underlying_symbol = None  # Will cause missing input

        _local_bar(db_session, good.id, START, close="215000")
        _underlying_bar(db_session, good.id, PREV, close="150", timestamp=PREV_NASDAQ_CLOSE_INSTANT)
        _fx_bar(db_session, START, close=Decimal("1000"))

        report = run_theoretical_ingestion(
            db_session,
            [good, bad],
            prediction_instant=BYMA_CLOSE_INSTANT,
        )

        assert report.status == "succeeded"
        assert report.theoretical_bars_inserted == 1
        assert any("missing input" in w for w in report.warnings)


class TestRefreshInstrumentTheoretical:
    """Per-symbol helper used by the task."""

    def test_stores_and_returns_tally(self, db_session: Session) -> None:
        """Returns a tally with insert/update counts."""
        instrument = _instrument(db_session)
        _local_bar(db_session, instrument.id, START, close="215000")
        _underlying_bar(
            db_session, instrument.id, PREV, close="150", timestamp=PREV_NASDAQ_CLOSE_INSTANT
        )
        _fx_bar(db_session, START, close=Decimal("1000"))

        tally = refresh_instrument_theoretical(
            db_session,
            instrument,
            prediction_instant=BYMA_CLOSE_INSTANT,
        )

        assert tally.inserted == 1
        assert tally.updated == 0


__all__: list[str] = []
