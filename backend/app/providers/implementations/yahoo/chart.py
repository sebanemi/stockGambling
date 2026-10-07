"""Yahoo Finance chart endpoint: URL building and payload parsing.

One endpoint, three fetch modes:

* **underlying** - ``AAPL``, ``VALE3.SA``, ``SIE.DE``, ``ULVR.L`` ... the
  vendor ticker is derived from the normalised market plus the underlying
  symbol;
* **local CEDEAR** - the same endpoint with a ``.BA`` suffix, which is how
  Yahoo quotes CEDEARs listed on BYMA in ARS;
* **FX** - ``USDARS=X``, the same chart machinery returning a single rate
  series.

The parsing layer is shared because the wire shape is identical; what differs
is the symbol fed in and the currency the vendor reports in its ``meta``.

**Time handling.** The ``timestamp`` array addresses each daily bar in the
exchange's *local* wall clock (09:30 ``America/New_York`` for a US bar). It is
decoded as an instant and re-projected through the exchange's IANA zone so the
resulting :attr:`~app.providers.base.DailyBar.market_date` is the venue's own
date and :attr:`~app.providers.base.DailyBar.timestamp` is the aware UTC
instant. A vendor change that re-stamped bars would then show up as a diff
rather than as a silent shift.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.providers.base import DailyBar, ProviderError, RejectedRecord

#: The chart endpoint accepts ``query1.finance.yahoo.com`` and answers index
#: notation for every equity, CEDEAR and currency pair Yahoo carries.
CHART_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart"
CHART_INTERVAL = "1d"

#: Yahoo crosses a currency pair to (and from) the platform's quote currency
#: with the ``=X`` suffix.
CROSS_SUFFIX = "=X"

#: Suffix for a security listed on Buenos Aires.
BYMA_SUFFIX = ".BA"

_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def chart_url(symbol: str, start: date, end: date) -> str:
    """Build the chart URL for ``symbol`` between ``start`` and ``end``.

    The range is inclusive on both ends, because the borrower of this URL asks
    for dates on the underlying's calendar and a half-open Unix epoch would
    drop the last trading day the caller asked for.
    """
    period1 = int(_date_to_epoch(start))
    period2 = int(_date_to_epoch(end)) + 86400
    return (
        f"{CHART_ENDPOINT}/{symbol}?period1={period1}&period2={period2}"
        f"&interval={CHART_INTERVAL}&includeAdjustedClose=true&events=history"
    )


def _date_to_epoch(day: date) -> float:
    """Naive local-midnight epoch Yahoo expects for a date."""
    return (
        datetime(day.year, day.month, day.day) - _UNIX_EPOCH.replace(tzinfo=None)
    ).total_seconds()


def _as_decimal(value: Any) -> Decimal | None:
    """A bar field as an exact :class:`~decimal.Decimal`, or ``None`` for gaps.

    Yahoo emits ``null`` and ``NaN`` where a trade did not happen; both mean
    "no observation", never zero.
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _as_volume(value: Any) -> int | None:
    """Volume as an int, or ``None`` for a missing observation."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _market_date_for(epoch: int, timezone_name: str) -> date:
    """The market date a Yahoo bar belongs to.

    The epoch Yahoo stores is an aware instant (the session start expressed in
    the exchange's local wall clock); projecting it through the exchange's IANA
    zone yields that venue's trading date, independent of the UTC clock at
    request time.
    """
    instant = datetime.fromtimestamp(epoch, tz=UTC)
    try:
        exchange_zone: Any = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        exchange_zone = UTC
    return instant.astimezone(exchange_zone).date()


def parse_chart(
    body: bytes,
    *,
    provider: str,
    source_url: str,
    display_symbol: str,
) -> tuple[tuple[DailyBar, ...], tuple[RejectedRecord, ...], dict[str, Any]]:
    """Parse a chart payload into bars, rejections and its ``meta``.

    Args:
        body: The raw chart-response body.
        provider: The provider name stamped on every bar.
        source_url: The URL the body was fetched from (provenance).
        display_symbol: The symbol labels on every bar; for an underlying,
            the ticker the platform thinks it is fetching, not the Yahoo form.

    Returns:
        ``(bars, rejected, meta)``. ``rejected`` carries the calendar slots
        that had no trade, so a quiet day is auditably distinct from a vendor
        that stopped answering. ``meta`` at least carries ``currency``, the
        exchange IANA timezone, and the Yahoo symbol actually consulted.

    Raises:
        ProviderError: if the body is not the expected shape, the chart
            carries an error, or there is nothing to parse.
    """
    import json

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError(f"{source_url} did not return JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ProviderError(f"{source_url} returned a non-object payload")
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise ProviderError(f"{source_url} did not return a 'chart' object")

    if chart.get("error"):
        raise ProviderError(f"{source_url} reports chart error: {chart['error']}")

    results = chart.get("result")
    if not isinstance(results, list) or not results:
        raise ProviderError(f"{source_url} returned no chart result for {display_symbol!r}")

    result = results[0]
    if not isinstance(result, dict):
        raise ProviderError(f"{source_url} returned a malformed chart result")

    meta = result.get("meta")
    if not isinstance(meta, dict):
        raise ProviderError(f"{source_url} returned a chart without meta")
    if meta.get("error"):
        raise ProviderError(f"{source_url} reports meta error: {meta['error']}")

    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        return (), (), meta

    quote = indicators.get("quote")
    quotes = quote[0] if isinstance(quote, list) and quote and isinstance(quote[0], dict) else {}
    adjclose_blocks = indicators.get("adjclose")
    adjclose_series = (
        adjclose_blocks[0].get("adjclose")
        if isinstance(adjclose_blocks, list)
        and adjclose_blocks
        and isinstance(adjclose_blocks[0], dict)
        else None
    )

    timezone_name = str(meta.get("exchangeTimezoneName") or "UTC")
    currency = str(meta.get("currency") or "USD") or "USD"

    lengths_ok = _aligned_lengths(timestamps, quotes)
    if not lengths_ok:
        raise ProviderError(
            f"{source_url} chart arrays are misaligned for {display_symbol!r}; "
            "the payload shape changed"
        )

    bars: list[DailyBar] = []
    rejected: list[RejectedRecord] = []
    for index, epoch in enumerate(timestamps):
        close = _as_decimal(_at(quotes, "close", index))
        if close is None or not isinstance(epoch, int | float):
            # A calendar slot with no trade is not a failed record; it is the
            # vendor honestly saying the market was quiet.
            rejected.append(
                RejectedRecord(
                    provider=provider,
                    symbol=display_symbol,
                    reason="no_trade_slot",
                    detail=f"timestamp {epoch!r} on {display_symbol} has no quotes",
                )
            )
            continue

        market_date = _market_date_for(int(epoch), timezone_name)
        adjusted = _as_decimal(_at_series(adjclose_series, index))
        volume = _as_volume(_at(quotes, "volume", index))
        bars.append(
            DailyBar(
                symbol=display_symbol,
                market_date=market_date,
                timestamp=datetime.fromtimestamp(int(epoch), tz=UTC),
                open=_as_decimal(_at(quotes, "open", index)),
                high=_as_decimal(_at(quotes, "high", index)),
                low=_as_decimal(_at(quotes, "low", index)),
                close=close,
                adjusted_close=adjusted,
                volume=volume,
                traded_value=None,
                trades=None,
                currency=currency,
                source=provider,
            )
        )

    return tuple(bars), tuple(rejected), meta


def _aligned_lengths(timestamps: list[Any], quotes: dict[str, Any]) -> bool:
    """Whether the timestamp and quote arrays agree in length.

    Yahoo emits parallel arrays; a length mismatch is a layout change and must
    abort rather than zip silently.
    """
    expected = len(timestamps)
    for field in ("open", "high", "low", "close", "volume"):
        values = quotes.get(field)
        if isinstance(values, list) and len(values) != expected:
            return False
    return True


def _at(quotes: dict[str, Any], field: str, index: int) -> Any:
    """The ``field`` value at ``index`` in a quote array, or ``None``."""
    values = quotes.get(field)
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def _at_series(series: Any, index: int) -> Any:
    """The ``index``-th adjusted close, or ``None`` when absent."""
    if not isinstance(series, list) or index >= len(series):
        return None
    return series[index]


__all__ = [
    "BYMA_SUFFIX",
    "CHART_ENDPOINT",
    "CHART_INTERVAL",
    "CROSS_SUFFIX",
    "chart_url",
    "parse_chart",
]
