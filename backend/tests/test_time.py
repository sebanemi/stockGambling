"""Timezone / market-session helper tests.

Cross-market timestamp handling is the primary source of look-ahead leakage
in a CEDEAR pipeline, so these invariants are pinned down early.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.core.time import (
    ARGENTINA_TZ,
    business_days_between,
    ensure_utc,
    is_within_session,
    local_session_bounds,
    to_market_date,
    utc_now,
)

pytestmark = pytest.mark.unit


def test_utc_now_is_aware() -> None:
    """`utc_now` must never return a naive datetime."""
    assert utc_now().tzinfo is not None


def test_naive_datetime_is_rejected() -> None:
    """Naive timestamps are ambiguous and must raise instead of guessing."""
    with pytest.raises(ValueError, match="Naive datetime"):
        ensure_utc(datetime(2026, 3, 10, 12, 0))


def test_ensure_utc_converts() -> None:
    """An aware datetime in any zone is normalised to UTC."""
    aware = datetime(2026, 3, 10, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    assert ensure_utc(aware) == datetime(2026, 3, 10, 16, 0, tzinfo=UTC)


def test_market_date_uses_market_timezone() -> None:
    """A UTC instant is bucketed into the *local* calendar date.

    22:00 UTC is already the next day in Buenos Aires; the New York session it
    belongs to is a different date again. Getting this wrong is precisely how
    a US close leaks into an Argentine prediction.
    """
    instant = datetime(2026, 3, 10, 22, 30, tzinfo=UTC)
    assert to_market_date(instant, ARGENTINA_TZ) == date(2026, 3, 10)
    assert to_market_date(instant, ZoneInfo("America/New_York")) == date(2026, 3, 10)

    late = datetime(2026, 3, 11, 2, 0, tzinfo=UTC)  # 23:00 ART on 2026-03-10
    assert to_market_date(late, ARGENTINA_TZ) == date(2026, 3, 10)


def test_byma_session_bounds() -> None:
    """BYMA session bounds are timezone-aware UTC instants."""
    start, end = local_session_bounds(date(2026, 3, 10))
    assert start.tzinfo is UTC and end.tzinfo is UTC
    assert start < end
    assert to_market_date(start, ARGENTINA_TZ) == date(2026, 3, 10)


def test_is_within_session() -> None:
    """Session membership uses local wall-clock time and is half-open."""
    inside = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)  # 12:00 ART
    before = datetime(2026, 3, 10, 14, 0, tzinfo=UTC)  # 11:00 ART
    closing = datetime(2026, 3, 10, 21, 0, tzinfo=UTC)  # exactly 18:00 ART
    assert is_within_session(inside, ARGENTINA_TZ, time(11, 30), time(18, 0)) is True
    assert is_within_session(before, ARGENTINA_TZ, time(11, 30), time(18, 0)) is False
    assert is_within_session(closing, ARGENTINA_TZ, time(11, 30), time(18, 0)) is False


def test_business_days_excludes_weekends() -> None:
    """Only Monday-Friday days are counted."""
    days = business_days_between(date(2026, 3, 9), date(2026, 3, 15))
    assert days == [
        date(2026, 3, 9),
        date(2026, 3, 10),
        date(2026, 3, 11),
        date(2026, 3, 12),
        date(2026, 3, 13),
    ]


def test_business_days_handles_inverted_range() -> None:
    """An inverted range yields no days rather than raising."""
    assert business_days_between(date(2026, 3, 15), date(2026, 3, 9)) == []
