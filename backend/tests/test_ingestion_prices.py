"""Price ingestion tests, against a real migrated PostgreSQL schema.

They prove the Phase 3 invariants only a database can: idempotent re-ingest,
in-place price revision, per-symbol failures becoming run warnings instead of
run failures, and one audit row capturing what the run consulted. Providers are
stubs returning crafted snapshots, so nothing reachable here is the network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.domain.vocabulary import UnderlyingMarket
from app.ingestion.prices import (
    JOB_NAME,
    backfill_window,
    refresh_fx,
    run_market_ingestion,
)
from app.models.instrument import IngestionRun, Instrument
from app.models.market_data import FxRate, LocalPriceBar, UnderlyingPriceBar
from app.providers.base import (
    BarSnapshot,
    DailyBar,
    FxBar,
    FXDataProvider,
    FxSnapshot,
    LocalPriceDataProvider,
    ProviderError,
    UnderlyingDataProvider,
)

pytestmark = pytest.mark.integration

START = date(2026, 7, 13)
END = date(2026, 7, 14)


def _daily(symbol: str, market_date: date, *, close: str) -> DailyBar:
    """One daily bar opening its session at 12:30 UTC on ``market_date``."""
    return DailyBar(
        symbol=symbol,
        market_date=market_date,
        timestamp=datetime(
            market_date.year, market_date.month, market_date.day, 12, 30, tzinfo=UTC
        ),
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("99"),
        close=Decimal(close),
        volume=1000,
        traded_value=Decimal("100000"),
        trades=42,
        currency="ARS",
        source="stub",
    )


def _snapshot(provider: str, bars: tuple[DailyBar, ...]) -> BarSnapshot:
    return BarSnapshot(
        provider=provider,
        source_url="https://stub.test/chart",
        fetched_at=utc_now(),
        bars=bars,
    )


def _fx(day: date, *, close: Decimal) -> FxBar:
    return FxBar(
        pair="USDARS",
        market_date=day,
        timestamp=datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC),
        open=close - Decimal("1"),
        high=close + Decimal("1"),
        low=close - Decimal("2"),
        close=close,
        currency="ARS",
        source="stub",
    )


def _fx_snapshot(bars: tuple[FxBar, ...]) -> FxSnapshot:
    return FxSnapshot(
        provider="stub",
        source_url="https://stub.test/chart",
        fetched_at=utc_now(),
        bars=bars,
    )


class StubLocalProvider(LocalPriceDataProvider):
    """Local series whose bars (or failures) the test fixes in advance."""

    name = "stub"

    def __init__(
        self,
        snapshot: BarSnapshot | None = None,
        error: str | None = None,
        *,
        fails: set[str] | None = None,
    ) -> None:
        """Wires the bars to serve and which symbols to refuse."""
        self.snapshot = snapshot
        self.error = error
        self.fails = fails or set()

    def fetch_daily_bars(self, cedear_symbol: str, start: date, end: date) -> BarSnapshot:
        """Serve the prebuilt bars, or raise for the symbols marked broken."""
        if cedear_symbol in self.fails:
            raise ProviderError(f"local feed is down for {cedear_symbol}")
        if self.error is not None:
            raise ProviderError(self.error)
        return self.snapshot or _snapshot(self.name, ())


class StubUnderlyingProvider(UnderlyingDataProvider):
    """Underlying series wired to fail or to return a fixed snapshot."""

    name = "stub"

    def __init__(self, snapshot: BarSnapshot | None = None, error: str | None = None) -> None:
        """Wires the bars to serve, the failure to raise, and a call log."""
        self.snapshot = snapshot
        self.error = error
        self.calls: list[tuple[str, UnderlyingMarket]] = []

    def fetch_daily_bars(
        self, symbol: str, market: UnderlyingMarket, start: date, end: date
    ) -> BarSnapshot:
        """Serve the bars, recording the (symbol, market) pair that was routed."""
        self.calls.append((symbol, market))
        if self.error is not None:
            raise ProviderError(self.error)
        return self.snapshot or _snapshot(self.name, ())


class StubFxProvider(FXDataProvider):
    """FX series that is either quiet or broken, per test."""

    name = "stub"

    def __init__(self, snapshot: FxSnapshot | None = None, error: str | None = None) -> None:
        """Wires the observations to serve and the failure to raise."""
        self.snapshot = snapshot
        self.error = error

    def fetch_daily(self, pair: str, start: date, end: date) -> FxSnapshot:
        """Serve the prebuilt observations, or raise when wired to fail."""
        if self.error is not None:
            raise ProviderError(self.error)
        return self.snapshot or _fx_snapshot(())


def _instrument(session: Session, symbol: str = "AAPL") -> Instrument:
    instrument = Instrument(
        symbol=symbol,
        name=f"{symbol} Inc",
        underlying_symbol=f"{symbol}.US",
        underlying_market=UnderlyingMarket.NASDAQ_GS.value,
        underlying_market_raw="NASDAQ_GS",
    )
    session.add(instrument)
    session.flush()
    return instrument


class TestRunMarketIngestion:
    """The full write path: three series, one audit row."""

    def test_everything_lands_in_its_own_table(self, db_session: Session) -> None:
        """Local, underlying and FX rows land in three separate tables."""
        instrument = _instrument(db_session)
        local = (
            _daily("AAPL", START, close="215.4"),
            _daily("AAPL", END, close="219.4"),
        )
        underlying = (
            _daily("AAPL.US", START, close="232.7"),
            _daily("AAPL.US", END, close="237.1"),
        )
        report = run_market_ingestion(
            db_session,
            [instrument],
            local_provider=StubLocalProvider(_snapshot("stub", local)),
            underlying_provider=StubUnderlyingProvider(_snapshot("stub", underlying)),
            fx_provider=StubFxProvider(
                _fx_snapshot(
                    (_fx(START, close=Decimal("1106.4")), _fx(END, close=Decimal("1111.7")))
                )
            ),
            start=START,
            end=END,
        )

        assert report.status == "succeeded"
        assert report.local_bars_inserted == 2
        assert report.underlying_bars_inserted == 2
        assert report.fx_bars_inserted == 2
        assert report.instruments == ("AAPL",)
        assert report.sources == ("stub", "stub", "stub")

        stored = db_session.scalar(
            select(LocalPriceBar).where(LocalPriceBar.instrument_id == instrument.id)
        )
        assert stored is not None
        assert stored.market_date == START
        assert stored.close == Decimal("215.4")
        assert stored.currency == "ARS"
        assert stored.source == "stub"
        assert stored.source_ref is None

        underlying_row = db_session.scalar(
            select(UnderlyingPriceBar).where(UnderlyingPriceBar.instrument_id == instrument.id)
        )
        assert underlying_row is not None
        assert underlying_row.symbol == "AAPL.US"
        assert underlying_row.close == Decimal("232.7")

        fx_row = db_session.scalar(select(FxRate).where(FxRate.pair == "USDARS"))
        assert fx_row is not None
        assert fx_row.close == Decimal("1106.4")
        assert fx_row.currency == "ARS"

    def test_the_run_row_records_what_was_consulted(self, db_session: Session) -> None:
        """The audit row captures job, sources and scope."""
        instrument = _instrument(db_session)
        run_market_ingestion(
            db_session,
            [instrument],
            local_provider=StubLocalProvider(),
            underlying_provider=StubUnderlyingProvider(),
            fx_provider=StubFxProvider(),
            start=START,
            end=END,
        )

        run = db_session.scalar(select(IngestionRun).order_by(IngestionRun.id.desc()))
        assert run is not None
        assert run.job == JOB_NAME
        assert run.status == "succeeded"
        assert run.sources == ["stub", "stub", "stub"]
        assert run.instruments_seen == 1
        assert run.error is None
        assert run.finished_at is not None

    def test_a_quiet_fetch_is_an_auditable_success(self, db_session: Session) -> None:
        """No bars is legal (holiday) and must not read as a failure."""
        instrument = _instrument(db_session)
        report = run_market_ingestion(
            db_session,
            [instrument],
            local_provider=StubLocalProvider(),
            underlying_provider=StubUnderlyingProvider(),
            fx_provider=StubFxProvider(),
            start=START,
            end=END,
        )
        assert report.status == "succeeded"
        assert report.local_bars_inserted == 0
        assert report.underlying_bars_inserted == 0
        assert report.fx_bars_inserted == 0

    def test_a_failed_symbol_narrows_the_report_but_not_the_run(self, db_session: Session) -> None:
        """One broken listing must not abort the universe, nor hide itself."""
        good = _instrument(db_session, "AAPL")
        broken = _instrument(db_session, "GOLD")
        report = run_market_ingestion(
            db_session,
            [good, broken],
            local_provider=StubLocalProvider(
                _snapshot("stub", (_daily("AAPL", START, close="215.4"),)),
                fails={"GOLD"},
            ),
            underlying_provider=StubUnderlyingProvider(error="the feed is down"),
            fx_provider=StubFxProvider(_fx_snapshot((_fx(START, close=Decimal("1106.4")),))),
            start=START,
            end=END,
        )

        assert report.status == "succeeded"
        assert report.local_bars_inserted == 1
        assert any("feed is down" in w for w in report.warnings)
        assert any("GOLD" in w for w in report.warnings)
        assert (
            db_session.scalar(
                select(func.count())
                .select_from(LocalPriceBar)
                .where(LocalPriceBar.instrument_id == good.id)
            )
            == 1
        )
        assert (
            db_session.scalar(
                select(func.count())
                .select_from(LocalPriceBar)
                .where(LocalPriceBar.instrument_id == broken.id)
            )
            == 0
        )

    def test_an_unknown_market_skips_the_underlying_with_a_warning(
        self, db_session: Session
    ) -> None:
        """An UNKNOWN venue is an honest gap, never a guessed series."""
        instrument = _instrument(db_session)
        instrument.underlying_market = UnderlyingMarket.UNKNOWN.value
        report = run_market_ingestion(
            db_session,
            [instrument],
            local_provider=StubLocalProvider(),
            underlying_provider=StubUnderlyingProvider(),
            fx_provider=StubFxProvider(),
            start=START,
            end=END,
        )
        assert report.status == "succeeded"
        assert report.underlying_bars_inserted == 0
        assert any("UNKNOWN" in w for w in report.warnings)


class TestReingestSemantics:
    """Re-running is a no-op, and a revision updates one row in place."""

    def test_a_second_run_reports_no_new_rows(self, db_session: Session) -> None:
        """Re-running identical bars is a zero-count pass."""
        instrument = _instrument(db_session)
        bars = (_daily("AAPL", START, close="215.4"),)
        options = {
            "local_provider": StubLocalProvider(_snapshot("stub", bars)),
            "underlying_provider": StubUnderlyingProvider(_snapshot("stub", bars)),
            "fx_provider": StubFxProvider(_fx_snapshot((_fx(START, close=Decimal("1106.4")),))),
            "start": START,
            "end": END,
        }
        run_market_ingestion(db_session, [instrument], **options)  # type: ignore[arg-type]

        again = run_market_ingestion(db_session, [instrument], **options)  # type: ignore[arg-type]
        assert again.local_bars_inserted == 0
        assert again.underlying_bars_inserted == 0
        assert again.fx_bars_inserted == 0
        assert again.local_bars_updated == 0
        assert again.underlying_bars_updated == 0

        assert (
            db_session.scalar(
                select(func.count())
                .select_from(LocalPriceBar)
                .where(LocalPriceBar.instrument_id == instrument.id)
            )
            == 1
        )

    def test_a_revised_close_updates_in_place_and_counts_once(self, db_session: Session) -> None:
        """A restated close revises one row and never duplicates it."""
        instrument = _instrument(db_session)
        run_market_ingestion(
            db_session,
            [instrument],
            local_provider=StubLocalProvider(
                _snapshot("stub", (_daily("AAPL", START, close="215.4"),))
            ),
            underlying_provider=StubUnderlyingProvider(),
            fx_provider=StubFxProvider(),
            start=START,
            end=END,
        )

        revised = run_market_ingestion(
            db_session,
            [instrument],
            local_provider=StubLocalProvider(
                _snapshot("stub", (_daily("AAPL", START, close="215.6"),))
            ),
            underlying_provider=StubUnderlyingProvider(),
            fx_provider=StubFxProvider(),
            start=START,
            end=END,
        )
        assert revised.local_bars_updated == 1
        row = db_session.scalar(
            select(LocalPriceBar).where(LocalPriceBar.instrument_id == instrument.id)
        )
        assert row is not None
        assert row.close == Decimal("215.6")
        assert (
            db_session.scalar(
                select(func.count())
                .select_from(LocalPriceBar)
                .where(LocalPriceBar.instrument_id == instrument.id)
            )
            == 1
        )

    def test_prices_round_to_the_stored_scale(self, db_session: Session) -> None:
        """A seven-decimal wire value lands at exactly six, no drift per pass."""
        instrument = _instrument(db_session)
        noisy = _daily("AAPL", START, close="215.40000001")
        run_market_ingestion(
            db_session,
            [instrument],
            local_provider=StubLocalProvider(_snapshot("stub", (noisy,))),
            underlying_provider=StubUnderlyingProvider(),
            fx_provider=StubFxProvider(),
            start=START,
            end=END,
        )
        row = db_session.scalar(
            select(LocalPriceBar).where(LocalPriceBar.instrument_id == instrument.id)
        )
        assert row is not None
        assert row.close == Decimal("215.400000")


class TestWindowAndRefresh:
    """The small helpers a backfill job leans on."""

    def test_backfill_window_days_zero_is_an_empty_window(self) -> None:
        """Days=0 requests nothing at all."""
        start, end = backfill_window(0)
        assert start == end

    def test_backfill_window_days_steps_calendar_days(self) -> None:
        """The window's length in calendar days matches the request."""
        start, end = backfill_window(60)
        assert (end - start).days == 60

    def test_refresh_fx_stores_into_sg_fx_history(self, db_session: Session) -> None:
        """The single-series helper persists into sg_fx_history."""
        fx = refresh_fx(
            db_session,
            StubFxProvider(_fx_snapshot((_fx(START, close=Decimal("1106.4")),))),
            pair="USDARS",
            start=START,
            end=END,
        )
        assert fx.inserted == 1
        row = db_session.scalar(select(FxRate).where(FxRate.pair == "USDARS"))
        assert row is not None
        assert row.market_date == START
