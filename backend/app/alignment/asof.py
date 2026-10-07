"""As-of alignment: join BYMA observations to underlying and FX series.

Cross-market joins are where look-ahead leakage gets introduced, so the only
join this platform performs between markets lives here, in one auditable,
testable module.

The contract, from ``docs/data-providers.md`` and ``docs/architecture.md``:

* A prediction at instant ``T`` may only use observations that existed at
  ``T``. Bars and rate observations carry aware UTC ``timestamp``s precisely so
  this rule is checkable on instants rather than on luck.
* The underlying series is **lagged one completed session** for the ``1d``
  horizon: a BYMA close at 18:00 ART on day *D* must not see that day's US
  close, which is future information from the modelling unit's point of view.
  ``market_date`` selection is therefore keyed on the prediction's *local* date
  in the underlying market's timezone, never on the UTC date.
* FX is strictly-as-of with **no lag**: the most recent observation whose
  timestamp is strictly before ``T``.

Both selectors are pure functions over the stored records: no database access,
no calendar knowledge beyond what the records themselves say. This keeps them
degenerately simple to test and impossible to unit-test wrongly.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.time import ensure_utc, to_market_date
from app.providers.base import DailyBar, FxBar

#: The horizon's lag: the ``n-th`` most recent completed underlying session,
#: where ``1`` means the most recent one that ended strictly before the
#: prediction's own market date.
DEFAULT_UNDERLYING_LAG = 1


def _completed_before(
    bars: list[DailyBar],
    prediction_instant: datetime,
    market_tz: ZoneInfo,
) -> list[DailyBar]:
    """Bars whose session completed before ``prediction_instant``.

    A session on market date ``m`` is eligible only when the prediction's local
    date in the underlying market is *after* ``m`` (that is the one-session
    lag: the day the prediction falls on is never an eligible session), and the
    bar's own instant is strictly before ``prediction_instant``.
    """
    prediction_date = to_market_date(prediction_instant, market_tz)
    eligible = [
        bar
        for bar in bars
        if bar.market_date < prediction_date and bar.timestamp < prediction_instant
    ]
    eligible.sort(key=lambda bar: (bar.market_date, bar.timestamp), reverse=True)
    return eligible


def select_underlying_bar(
    bars: list[DailyBar],
    prediction_instant: datetime,
    market_tz: ZoneInfo,
    *,
    lag_sessions: int = DEFAULT_UNDERLYING_LAG,
) -> DailyBar | None:
    """The underlying bar a prediction at ``prediction_instant`` may use.

    Args:
        bars: The instrument's underlying bars (one row per market date is the
            schema's guarantee, not a requirement here).
        prediction_instant: The moment the prediction is made, timezone-aware.
        market_tz: The **underlying market's** timezone. The prediction's own
            BYMA date must never be computed with this zone.
        lag_sessions: How many completed sessions to step back. ``1`` (the
            default, and the ``1d`` horizon's value) means "most recent
            completed session".

    Returns:
        The chosen bar, or ``None`` when no eligible session exists (too little
        history, or a prediction before any session completed). ``None`` is
        honest: callers must treat it as a missing feature, never substitute
        the nearest past bar.
    """
    if lag_sessions < 1:
        raise ValueError(f"lag_sessions must be at least 1, got {lag_sessions}")
    if not bars:
        return None

    eligible = _completed_before(bars, prediction_instant, market_tz)
    index = lag_sessions - 1
    if index >= len(eligible):
        return None
    return eligible[index]


def select_fx_observation(
    observations: list[FxBar],
    prediction_instant: datetime,
) -> FxBar | None:
    """The most recent FX observation published strictly before ``T``.

    FX has no lag: the boundary is the instant itself. An observation whose
    timestamp equals ``T`` is *not* eligible, so a test can assert the exact
    cutoff rather than an approximation.
    """
    instant = ensure_utc(prediction_instant)
    eligible = [obs for obs in observations if obs.timestamp < instant]
    if not eligible:
        return None
    return max(eligible, key=lambda obs: (obs.market_date, obs.timestamp))


def market_dates(bars: list[DailyBar]) -> list[date]:
    """Distinct market dates in ascending order, for reports and tests."""
    return sorted({bar.market_date for bar in bars})


__all__ = [
    "DEFAULT_UNDERLYING_LAG",
    "market_dates",
    "select_fx_observation",
    "select_underlying_bar",
]
