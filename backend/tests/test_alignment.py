"""As-of alignment tests: the anti-leakage boundary, whitespace, and honesty.

The module is pure (sequences of records in, one record out), so every leakage
mode is testable without a database: same-session bars are invisible, the lag
steps calendar sessions not instants, and an instant that equals a bar's own
timestamp must not admit it. All timestamps here are aware UTC; a naive
datetime is refuse-with-error.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.alignment.asof import (
    market_dates,
    select_fx_observation,
    select_underlying_bar,
)
from app.providers.base import DailyBar, FxBar

pytestmark = pytest.mark.unit

NEW_YORK = ZoneInfo("America/New_York")

FRI = date(2026, 7, 10)
MON = date(2026, 7, 13)


def _bars_for(days: list[date]) -> list[DailyBar]:
    """Build a daily series; each bar opens its session at 13:30 UTC on its day."""
    out: list[DailyBar] = []
    for day in days:
        ts = datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC)
        out.append(
            DailyBar(
                symbol="AAPL",
                market_date=day,
                timestamp=ts,
                close=Decimal("200"),
            )
        )
    return out


class TestUnderlyingLag:
    """The one-session lag is what makes the horizon leak-free."""

    def test_a_same_session_bar_is_invisible(self) -> None:
        """A prediction during day D must not see day D's underlying session."""
        series = _bars_for([FRI, MON])
        # During the Monday US session (15:30 ET), Monday's own bar is infraday.
        prediction = datetime(2026, 7, 13, 15, 30, tzinfo=UTC)
        selected = select_underlying_bar(series, prediction, NEW_YORK)
        assert selected is not None
        assert selected.market_date == FRI  # the last completed session

    def test_the_default_lag_is_one(self) -> None:
        """The 1d horizon's value is explicit and the default."""
        from app.alignment import DEFAULT_UNDERLYING_LAG

        assert DEFAULT_UNDERLYING_LAG == 1

    def test_lag_two_steps_two_sessions(self) -> None:
        """A deeper lag for longer horizons walks older completed sessions."""
        series = _bars_for([date(2026, 7, 8), FRI, MON])
        prediction = datetime(2026, 7, 13, 15, 30, tzinfo=UTC)
        selected = select_underlying_bar(series, prediction, NEW_YORK, lag_sessions=2)
        assert selected is not None
        assert selected.market_date == date(2026, 7, 8)

    def test_weekend_prediction_uses_last_friday(self) -> None:
        """A Sunday instant falls *after* Friday's completed session."""
        series = _bars_for([FRI, date(2026, 7, 17)])
        prediction = datetime(2026, 7, 19, 13, 0, tzinfo=UTC)  # Sunday
        selected = select_underlying_bar(series, prediction, NEW_YORK)
        assert selected is not None
        assert selected.market_date == date(2026, 7, 17)

    def test_insufficient_history_returns_none(self) -> None:
        """An honest None, never the nearest past bar as a substitute."""
        series = _bars_for([MON, date(2026, 7, 14)])
        prediction = datetime(2026, 7, 13, 15, 30, tzinfo=UTC)
        assert select_underlying_bar(series, prediction, NEW_YORK) is None
        assert select_underlying_bar([], prediction, NEW_YORK) is None

    def test_a_bar_published_at_the_instant_is_ineligible(self) -> None:
        """Equality is not eligibility: timestamp == T must not pass."""
        series = _bars_for([FRI])
        prediction = datetime(2026, 7, 10, 13, 30, tzinfo=UTC)
        assert select_underlying_bar(series, prediction, NEW_YORK) is None

    def test_zero_lag_is_refused(self) -> None:
        """A lag of 0 would admit the running session; it is an API error."""
        series = _bars_for([FRI, MON])
        with pytest.raises(ValueError, match="at least 1"):
            select_underlying_bar(series, datetime.now(UTC), NEW_YORK, lag_sessions=0)


class TestFxStrictlyBefore:
    """FX has no lag: the boundary is the instant itself."""

    def observation(self, day: date, *, hour: int = 14, minute: int = 30) -> FxBar:
        """A daily USDARS observation published at ``day`` 14:30 UTC."""
        return FxBar(
            pair="USDARS",
            market_date=day,
            timestamp=datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC),
            close=Decimal("1100"),
        )

    def test_the_most_recent_published_observation_is_used(self) -> None:
        """A prediction after Wednesday's print sees Wednesday's rate."""
        obs = [self.observation(d) for d in (date(2026, 7, 10), date(2026, 7, 13))]
        prediction = datetime(2026, 7, 13, 18, 0, tzinfo=UTC)
        selected = select_fx_observation(obs, prediction)
        assert selected is not None
        assert selected.market_date == date(2026, 7, 13)

    def test_a_print_at_the_instant_is_not_eligible(self) -> None:
        """FX published *at* T is as future as the next minute."""
        obs = [self.observation(date(2026, 7, 13))]
        prediction = datetime(2026, 7, 13, 14, 30, tzinfo=UTC)
        assert select_fx_observation(obs, prediction) is None

    def test_no_prior_observation_returns_none(self) -> None:
        """A prediction before any print has nothing honest to return."""
        obs = [self.observation(date(2026, 7, 13))]
        prediction = datetime(2026, 7, 10, 14, 30, tzinfo=UTC)
        assert select_fx_observation(obs, prediction) is None

    def test_a_naive_instant_is_refused(self) -> None:
        """A naive datetime is ambiguous and must not reach the boundary."""
        obs = [self.observation(date(2026, 7, 13))]
        with pytest.raises(ValueError, match="Naive"):
            select_fx_observation(obs, datetime(2026, 7, 13, 18, 0))


class TestMarketDates:
    """The tiny reporting helper, pinned down while it is still tiny."""

    def test_distinct_ascending_with_no_day_numbers_lost(self) -> None:
        """market_dates collapses duplicates and sorts, for reporting."""
        observed = _bars_for([MON, FRI, MON])
        assert market_dates(observed) == [FRI, MON]

    def test_empty(self) -> None:
        """An empty series yields no dates at all."""
        assert market_dates([]) == []
