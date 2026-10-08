"""Leakage tests: no feature may see data published after its `as_of`.

These encode the scientific-integrity rules: features and evaluation are
pure functions of history strictly eligible at the prediction instant, and
the build fails if a leak is introduced. Database-backed cases skip cleanly
when PostgreSQL is unreachable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select
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

    def test_same_instant_in_any_timezone_gives_same_market_date(self) -> None:
        """The BYMA date of an instant never depends on how it is expressed."""
        from zoneinfo import ZoneInfo

        from app.core.time import to_market_date

        utc = datetime(2026, 3, 10, 21, 0, tzinfo=UTC)
        art = utc.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
        assert to_market_date(utc) == to_market_date(art) == D


def _find_app_root(start: Path) -> Path | None:
    """Locate ``app/`` for the shuffle scan; ``None`` inside the image."""
    for candidate in [start, *start.parents]:
        if (candidate / "app").is_dir() and (candidate / "pyproject.toml").exists():
            return candidate
    return None


class TestNoShuffledSplits:
    """Enabling a shuffle anywhere under ``app/`` fails the build."""

    def test_no_shuffle_in_application_code(self) -> None:
        """Time never shuffles: no split, sampler or frame may enable it."""
        import re

        root = _find_app_root(Path(__file__).resolve().parent)
        if root is None:
            pytest.skip("application source tree not available (e.g. inside the API image)")
        pattern = re.compile(r"shuffle\s*=\s*True")
        offenders = [
            str(path)
            for path in (root / "app").rglob("*.py")
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
        ]
        assert not offenders, f"shuffle enabled in application code:\n{offenders}"

    def test_walk_forward_folds_preserve_input_order(self) -> None:
        """Within each fold positions ascend and train precedes test."""
        from app.evaluation.splits import walk_forward_folds

        folds = walk_forward_folds(12, min_train=4, test_size=2, step=2)
        for fold in folds:
            window = list(range(fold.train_start, fold.test_end))
            assert window == sorted(window)
            assert fold.train_end <= fold.test_start


class TestTrainOnlyStatistics:
    """Imputation statistics come from training windows alone."""

    def test_medians_match_train_window_only(self) -> None:
        """Evaluation rows never move the stored medians."""
        from app.modeling.baselines import LogisticRegressionModel

        rng = np.random.default_rng(5)
        train = rng.normal(loc=0.0, size=(40, 3))
        test = rng.normal(loc=100.0, size=(10, 3))  # shifted distribution
        y_train = (train[:, 0] > 0).astype(np.int64)
        model = LogisticRegressionModel().fit(train, y_train)
        assert model.medians_ is not None
        assert np.allclose(model.medians_, np.nanmedian(train, axis=0))
        before = model.medians_.copy()
        model.predict(test)
        assert np.array_equal(model.medians_, before)


class TestFutureConversionRatios:
    """A ratio change affects only dates on/after its effective date."""

    def test_later_ratio_row_does_not_rewrite_history(self, db_session: Session) -> None:
        """The theoretical value for D is identical before/after the change."""
        from app.models.instrument import InstrumentRatioHistory
        from app.theoretical.engine import build_theoretical_result

        inst = _instrument(db_session, symbol="LEAK6")
        inst.underlying_symbol = "LEAK6-U"
        inst.underlying_market = "NASDAQ_GS"
        inst.underlying_market_raw = "NASDAQ_GS"
        db_session.add(
            InstrumentRatioHistory(
                instrument_id=inst.id,
                ratio=Decimal("10"),
                effective_from=date(2026, 1, 1),
                effective_to=None,
                source="test",
                source_ref="test",
            )
        )
        _underlying(db_session, inst, date(2026, 3, 9), "150")
        _fx(db_session, date(2026, 3, 9), "1000")
        _fx(db_session, D, "1000")
        db_session.commit()

        before = build_theoretical_result(db_session, inst, _as_of())
        assert before is not None

        current = db_session.scalar(
            select(InstrumentRatioHistory).where(
                InstrumentRatioHistory.instrument_id == inst.id,
                InstrumentRatioHistory.effective_to.is_(None),
            )
        )
        assert current is not None
        current.effective_to = date(2026, 3, 11)
        db_session.add(
            InstrumentRatioHistory(
                instrument_id=inst.id,
                ratio=Decimal("999"),
                effective_from=date(2026, 3, 11),
                effective_to=None,
                source="test",
                source_ref="test",
            )
        )
        db_session.commit()

        after = build_theoretical_result(db_session, inst, _as_of())
        assert after is not None
        assert after.theoretical_price == before.theoretical_price
        assert after.ratio_used == Decimal("10")


class TestFutureVolume:
    """Volume spikes published on D never reach features computed at D."""

    def test_same_day_volume_spike_is_invisible(self, db_session: Session) -> None:
        """The 5-day volume change uses strictly prior sessions."""
        from app.features.underlying_technical import compute_underlying_technical_features

        inst = _instrument(db_session, symbol="LEAK7")
        for i, day in enumerate(date(2026, 3, d) for d in range(1, 10)):
            db_session.add(
                UnderlyingPriceBar(
                    instrument_id=inst.id,
                    symbol="LEAK7-U",
                    market_date=day,
                    timestamp=_utc_noon(day),
                    open=Decimal("100"),
                    high=Decimal("101"),
                    low=Decimal("99"),
                    close=Decimal(str(100 + i)),
                    volume=1000,
                    currency="USD",
                    source="test",
                )
            )
        spike = UnderlyingPriceBar(
            instrument_id=inst.id,
            symbol="LEAK7-U",
            market_date=D,
            timestamp=_utc_noon(D),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("110"),
            volume=10_000_000,  # same-day spike: must be ignored
            currency="USD",
            source="test",
        )
        db_session.add(spike)
        db_session.commit()

        result = compute_underlying_technical_features(
            get_feature_registry(), inst.id, _as_of(), db_session
        )
        # Change over the last eligible sessions: 1000 -> 1000 is flat.
        assert result["underlying_volume_change_5d"] == pytest.approx(0.0)
