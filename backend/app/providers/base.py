"""Provider interfaces and the vendor-neutral records they return.

The application never imports a vendor payload type. Vendors are translated
here, once, into the vocabulary the rest of the platform speaks.

Two contracts matter most for correctness:

* :class:`CedearRecord.ratio` is a :class:`~decimal.Decimal` count of CEDEARs
  per underlying unit, or ``None`` when the source did not publish an
  unambiguous value. It is never a guess.
* :class:`CedearSnapshot.rejected` carries every record the adapter refused,
  with the reason. An adapter that silently drops rows is indistinguishable
  from one that is working, so rejection is part of the interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.domain.vocabulary import InstrumentType, ProgramStatus, UnderlyingMarket, normalise_market

#: Column contract for price series, per ``docs/data-providers.md``. Every
#: Phase 3 provider produces bars carrying exactly these fields (see
#: :class:`DailyBar`).
UNDERLYING_BAR_COLUMNS: tuple[str, ...] = (
    "symbol",
    "market_date",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "volume",
    "traded_value",
    "trades",
    "currency",
    "source",
)


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One day's OHLCV bar for a listed security.

    The two time notions are deliberately separate, per the as-of contract:

    * ``market_date`` is the **market's own** trading date (``2026-03-02`` for
      a BYMA session, regardless of the UTC clock),
    * ``timestamp`` is the aware UTC instant the bar represents (for Yahoo,
      the session start as Yahoo stamps it, converted to UTC).

    ``traded_value`` and ``trades`` are ``None`` when the vendor does not
    publish them. ``adjusted_close`` is ``None`` rather than a copy of
    ``close`` when the vendor does not adjust, so consumers never confuse the
    two. Numbers are ``Decimal`` so ratios and prices stay exact; ``NaN`` from
    a wire payload maps to ``None``.
    """

    symbol: str
    market_date: date
    timestamp: datetime

    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    adjusted_close: Decimal | None = None

    volume: int | None = None
    traded_value: Decimal | None = None
    trades: int | None = None

    currency: str = "USD"
    source: str = "abstract"

    def __repr__(self) -> str:
        """Readable representation for assertion failures."""
        return (
            f"<DailyBar {self.symbol} {self.market_date.isoformat()} "
            f"close={self.close!r} source={self.source}>"
        )


@dataclass(frozen=True, slots=True)
class FxBar:
    """One day's rate observation for a currency pair.

    Only the rate fields are required; ``volume`` is kept for parity with
    :class:`DailyBar` but rate sources rarely publish a meaningful one.
    """

    pair: str
    market_date: date
    timestamp: datetime

    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None

    #: The quote currency, i.e. the platform side of the pair (``ARS`` for
    #: ``USDARS``). The platform prices CEDEARs in ARS.
    currency: str = "ARS"
    source: str = "abstract"
    volume: int | None = None


@dataclass(frozen=True, slots=True)
class BarSnapshot:
    """The result of one price fetch, with the same honesty as a metadata snapshot.

    Provenance is recorded, per-bar problems land in ``rejected``, and a fetch
    whose bars were unusable is distinguishable from a market that was
    genuinely quiet.
    """

    provider: str
    source_url: str
    fetched_at: datetime
    bars: tuple[DailyBar, ...] = ()
    rejected: tuple[RejectedRecord, ...] = ()
    warnings: tuple[str, ...] = ()

    def by_date(self) -> dict[date, DailyBar]:
        """Index the bars by their market date."""
        return {bar.market_date: bar for bar in self.bars}


@dataclass(frozen=True, slots=True)
class FxSnapshot:
    """The result of one FX fetch, mirroring :class:`BarSnapshot`."""

    provider: str
    source_url: str
    fetched_at: datetime
    bars: tuple[FxBar, ...] = ()
    rejected: tuple[RejectedRecord, ...] = ()
    warnings: tuple[str, ...] = ()

    def by_date(self) -> dict[date, FxBar]:
        """Index the observations by their market date."""
        return {bar.market_date: bar for bar in self.bars}


@dataclass(frozen=True, slots=True)
class CedearRecord:
    """One CEDEAR as published by a single source.

    The CEDEAR and its underlying are separate fields on purpose. They are
    frequently *different* tickers (BYMA ``BPA11`` wraps ``BPAC11``; ``SI``
    wraps the delisted ``SICPQ``), so deriving one from the other would be
    wrong.
    """

    symbol: str
    custodian: str
    source_ref: str | None = None

    name: str | None = None
    instrument_type: InstrumentType = InstrumentType.UNKNOWN

    underlying_symbol: str | None = None
    underlying_name: str | None = None
    #: Market exactly as published. Kept because the normalised value may be
    #: ``UNKNOWN`` and that must remain visible rather than becoming a fact.
    underlying_market_raw: str | None = None
    underlying_isin: str | None = None

    #: CEDEARs per underlying unit. ``Decimal("60")`` for ``60:1``,
    #: ``Decimal("0.2")`` for ``1:5``. ``None`` when not published unambiguously.
    ratio: Decimal | None = None

    isin: str | None = None

    program_status: ProgramStatus = ProgramStatus.UNKNOWN
    program_status_raw: str | None = None

    #: Anything the source published that has no typed column.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def underlying_market(self) -> UnderlyingMarket:
        """The normalised venue for this record's underlying."""
        return normalise_market(self.underlying_market_raw)

    @property
    def is_tradeable_reference(self) -> bool:
        """Whether the record carries a usable underlying identification.

        An instrument without an underlying ticker cannot be linked to a price
        series, so downstream stages must treat it as incomplete rather than
        silently matching it to a same-named local listing. An empty or
        whitespace-only ticker is treated as absent: the feeds use it to mean
        "not published", and it would otherwise pass as a reference.
        """
        return bool(self.underlying_symbol and self.underlying_symbol.strip())


@dataclass(frozen=True, slots=True)
class RejectedRecord:
    """A source record the adapter refused, and why.

    Persisted with the ingestion run so that a shrinking universe is
    explainable after the fact.
    """

    provider: str
    reason: str
    detail: str
    symbol: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form for the ingestion-run audit record."""
        return {
            "provider": self.provider,
            "symbol": self.symbol,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class CedearSnapshot:
    """The result of one fetch from one provider.

    Immutable so that a snapshot can be compared across runs and a regression
    in a parser shows up as a diff rather than as a quiet drift.
    """

    provider: str
    source_url: str
    fetched_at: datetime
    records: tuple[CedearRecord, ...] = ()
    rejected: tuple[RejectedRecord, ...] = ()
    #: Non-fatal observations, e.g. an unrecognised market spelling.
    warnings: tuple[str, ...] = ()

    def by_symbol(self) -> dict[str, CedearRecord]:
        """Index the records by CEDEAR symbol."""
        return {record.symbol: record for record in self.records}


class ProviderError(RuntimeError):
    """A provider could not be fetched or parsed at all.

    Distinct from a per-record rejection: this means the run cannot be trusted
    and must not be allowed to mark instruments inactive.
    """


class CedearDataProvider(ABC):
    """Supplies the CEDEAR universe: symbol, underlying, exchange, ratio, type.

    Implementations must not assume completeness. No single official source
    covers every listed program, so the ingestion service unions several
    providers and records which ones contributed.
    """

    #: Stable identifier used in settings, in the registry and as the stored
    #: ``source`` on ratio rows.
    name: str = "abstract"

    @property
    @abstractmethod
    def source_url(self) -> str:
        """The URL (or document) the data is read from."""

    @abstractmethod
    def fetch(self) -> CedearSnapshot:
        """Read the source and return a snapshot.

        Raises:
            ProviderError: if the source is unreachable or structurally
                unrecognisable. Per-record problems must be reported through
                :attr:`CedearSnapshot.rejected` instead of raising.
        """


class UnderlyingDataProvider(ABC):
    """Supplies OHLCV for a foreign symbol, in the **underlying's** timezone.

    Defined in Phase 2 so the interface is settled before any vendor is chosen;
    concrete implementations arrive in Phase 3. The three rules that matter are
    inherited from ``docs/data-providers.md``: timestamps are timezone-aware
    UTC, market dates are the provider's own local dates, and ``adjusted_close``
    is ``None`` rather than substituted with ``close`` when unavailable.
    """

    name: str = "abstract"

    @abstractmethod
    def fetch_daily_bars(
        self,
        symbol: str,
        market: UnderlyingMarket,
        start: date,
        end: date,
    ) -> BarSnapshot:
        """Return daily bars for ``symbol`` between ``start`` and ``end``.

        Args:
            symbol: The underlying's ticker as the vendor expects it.
            market: The normalised venue, used to build the vendor's symbol
                (e.g. Yahoo's ``.SA`` suffix for B3). An ``UNKNOWN`` market
                cannot be fetched and should raise :class:`ProviderError`.

        Returns:
            A :class:`BarSnapshot` whose bars carry exactly the
            :data:`UNDERLYING_BAR_COLUMNS` fields.

        Raises:
            ProviderError: if the source is unreachable, structurally
                unrecognisable, or the market cannot be routed to a symbol.
        """


class LocalPriceDataProvider(ABC):
    """Supplies OHLCV for a CEDEAR as quoted, in the Argentine market.

    History is local (``AAPL`` CEDEAR on BYMA, in ARS). Nothing here is
    adjusted: BYMA publishes what traded. ``adjusted_close`` on the returned
    bars is ``None`` unless the vendor actually adjusts local listings.
    """

    name: str = "abstract"

    @abstractmethod
    def fetch_daily_bars(
        self,
        cedear_symbol: str,
        start: date,
        end: date,
    ) -> BarSnapshot:
        """Return daily bars for the local CEDEAR between ``start`` and ``end``.

        Returns:
            A :class:`BarSnapshot` in ARS, dated on the BYMA calendar.

        Raises:
            ProviderError: if the source is unreachable or unrecognisable.
        """


class FXDataProvider(ABC):
    """Supplies a daily FX series with an explicit market calendar.

    The default pair is ``USDARS``; the rate is ARS per USD, the convention the
    platform prices everything in. Only observations *published before* a
    prediction instant are ever eligible downstream (no same-day FX as
    of a BYMA close that precedes it).
    """

    name: str = "abstract"

    @abstractmethod
    def fetch_daily(
        self,
        pair: str,
        start: date,
        end: date,
    ) -> FxSnapshot:
        """Return daily rate observations for ``pair`` between ``start`` and ``end``.

        Returns:
            A :class:`FxSnapshot`; ``pair`` on every bar is fixed to what was
            requested, never made up.

        Raises:
            ProviderError: if the source is unreachable or unrecognisable.
        """


__all__ = [
    "UNDERLYING_BAR_COLUMNS",
    "BarSnapshot",
    "CedearDataProvider",
    "CedearRecord",
    "CedearSnapshot",
    "DailyBar",
    "FXDataProvider",
    "FxBar",
    "FxSnapshot",
    "LocalPriceDataProvider",
    "ProviderError",
    "RejectedRecord",
    "UnderlyingDataProvider",
]
