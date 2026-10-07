"""Timezone and trading-calendar helpers.

StockGambling is a *multi-market* system: BYMA (Argentina) and the underlying
US/European markets never share a session. Every timestamp handled here is
timezone-aware and every join between markets must go through these helpers so
that "the Argentine close" is never confused with "the US close".
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

#: Argentine local time. Since 2009 Argentina has been on UTC-03:00 with no DST.
ARGENTINA_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

#: Timezone for each underlying market. Used by the as-of rule and theoretical engine.
MARKET_TIMEZONES: dict[str, ZoneInfo] = {
    "NYSE": ZoneInfo("America/New_York"),
    "NYSE_AMERICAN": ZoneInfo("America/New_York"),
    "NYSE_ARCA": ZoneInfo("America/New_York"),
    "NASDAQ_GS": ZoneInfo("America/New_York"),
    "NASDAQ_GM": ZoneInfo("America/New_York"),
    "NASDAQ_CM": ZoneInfo("America/New_York"),
    "CBOE_BZX": ZoneInfo("America/New_York"),
    "OTC": ZoneInfo("America/New_York"),
    "B3": ZoneInfo("America/Sao_Paulo"),
    "XETRA": ZoneInfo("Europe/Berlin"),
    "LSE": ZoneInfo("Europe/London"),
    "UNKNOWN": ZoneInfo("UTC"),
}

#: BYMA continuous trading session (11:30-18:00 ART, historical convention).
BYMA_SESSION_OPEN = time(11, 30)
BYMA_SESSION_CLOSE = time(18, 0)

#: Regular US equity session in New York local time (09:30-16:00 ET).
US_EQUITY_SESSION_OPEN = time(9, 30)
US_EQUITY_SESSION_CLOSE = time(16, 0)


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as timezone-aware UTC.

    Naive datetimes are rejected: silently assuming a timezone is one of the
    main sources of look-ahead leakage in cross-market pipelines.
    """
    if value.tzinfo is None:
        raise ValueError(
            "Naive datetime received. Timezone-naive timestamps are ambiguous and "
            "are not accepted; pass an aware datetime (e.g. via ZoneInfo)."
        )
    return value.astimezone(UTC)


def to_timezone(value: datetime, tz: ZoneInfo) -> datetime:
    """Convert an aware datetime into the requested timezone."""
    return ensure_utc(value).astimezone(tz)


def to_market_date(value: datetime, tz: ZoneInfo = ARGENTINA_TZ) -> date:
    """Return the *local* calendar date of ``value`` for the market at ``tz``.

    This is the correct way to bucket a UTC instant into an Argentine trading
    day. The underlying-market date must be computed with the *underlying's*
    timezone, never this one.
    """
    return to_timezone(value, tz).date()


def is_within_session(value: datetime, tz: ZoneInfo, open_: time, close: time) -> bool:
    """Whether an aware timestamp falls inside a regular trading session."""
    local = to_timezone(value, tz)
    return open_ <= local.time() < close


def local_session_bounds(
    session_date: date, tz: ZoneInfo = ARGENTINA_TZ
) -> tuple[datetime, datetime]:
    """Return the (open, close) instants of a local session for ``session_date``.

    The returned instants are timezone-aware and expressed in UTC, which is how
    they are persisted.
    """
    start = datetime.combine(session_date, BYMA_SESSION_OPEN, tzinfo=tz)
    end = datetime.combine(session_date, BYMA_SESSION_CLOSE, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def business_days_between(start: date, end: date) -> list[date]:
    """Return the Monday-Friday calendar days in ``[start, end]``.

    Only a coarse approximation of "relevant trading sessions": exchange
    holidays are not modelled yet and are added in a later phase. Used only
    for horizon arithmetic and reporting, never for feature construction.
    """
    if end < start:
        return []
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days
