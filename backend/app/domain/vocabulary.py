"""Domain vocabulary shared by the provider layer, the ORM and the API.

The official sources publish free-text values that are *not* stable, *not*
uniform and occasionally *wrong*. This module is the single place where those
values are translated into the platform's own vocabulary, so that a vendor
quirk is fixed once and audited in one file.

Two rules govern everything here:

1. **Never guess.** An unrecognised value becomes :attr:`UnderlyingMarket.UNKNOWN`
   and is reported as a warning. The raw string is always preserved alongside
   the normalised one so a later run can reconsider it without losing data.
2. **Never silently coerce.** Conflating "New York" with "NYSE", or with
   "NYSE American", would fabricate a fact that changes how a price series is
   later retrieved. Ambiguity is surfaced, not resolved by guesswork.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo


class InstrumentType(StrEnum):
    """What the CEDEAR represents.

    The distinction matters downstream: an ETF CEDEAR tracks a fund whose
    "underlying ticker" is itself a listed security, while a stock CEDEAR
    tracks a share of a company. Both must be priced through the same
    theoretical formula, but they are not the same kind of object.
    """

    STOCK = "STOCK"
    ETF = "ETF"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class UnderlyingMarket(StrEnum):
    """Canonical codes for the market where the underlying trades.

    These are the venue identities the platform reasons about. The values are
    stable, uppercase and vendor-neutral; the per-source spellings are mapped
    onto them by :func:`normalise_market`.
    """

    NYSE = "NYSE"
    NYSE_AMERICAN = "NYSE_AMERICAN"
    NYSE_ARCA = "NYSE_ARCA"
    NASDAQ_GS = "NASDAQ_GS"
    NASDAQ_GM = "NASDAQ_GM"
    NASDAQ_CM = "NASDAQ_CM"
    CBOE_BZX = "CBOE_BZX"
    OTC = "OTC"
    B3 = "B3"
    XETRA = "XETRA"
    LSE = "LSE"
    UNKNOWN = "UNKNOWN"

    @property
    def tz(self) -> ZoneInfo:
        """The timezone of this market's trading session."""
        from app.core.time import MARKET_TIMEZONES

        return MARKET_TIMEZONES[self]


class ProgramStatus(StrEnum):
    """Whether the issuer still allows the program to issue or cancel CEDEARs.

    This is *not* the same as :attr:`Instrument.is_active`. A program can be
    disabled for issuance while its CEDEARs remain listed and quoted on BYMA,
    so disabling issuance must not make the instrument disappear from the API.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    UNKNOWN = "UNKNOWN"


#: Source spellings that map unambiguously onto a canonical market.
#:
#: Only exact, single-venue spellings belong here. Anything that could denote
#: more than one venue ("New York", "US") is intentionally absent so that it
#: degrades to ``UNKNOWN`` instead of becoming a wrong fact.
_MARKET_ALIASES: Final[dict[str, UnderlyingMarket]] = {
    "nyse": UnderlyingMarket.NYSE,
    "new york stock exchange": UnderlyingMarket.NYSE,
    "nyseamerican": UnderlyingMarket.NYSE_AMERICAN,
    "nyse american": UnderlyingMarket.NYSE_AMERICAN,
    "nyse amex": UnderlyingMarket.NYSE_AMERICAN,
    "amex": UnderlyingMarket.NYSE_AMERICAN,
    "nyse arca": UnderlyingMarket.NYSE_ARCA,
    "nysearca": UnderlyingMarket.NYSE_ARCA,
    "arca": UnderlyingMarket.NYSE_ARCA,
    "nasdaq gs": UnderlyingMarket.NASDAQ_GS,
    "nasdaqgs": UnderlyingMarket.NASDAQ_GS,
    "nasdaq global select": UnderlyingMarket.NASDAQ_GS,
    "nasdaq gm": UnderlyingMarket.NASDAQ_GM,
    "nasdaqgm": UnderlyingMarket.NASDAQ_GM,
    "nasdaq global market": UnderlyingMarket.NASDAQ_GM,
    "nasdaq cm": UnderlyingMarket.NASDAQ_CM,
    "nasdaqcm": UnderlyingMarket.NASDAQ_CM,
    "nasdaq capital market": UnderlyingMarket.NASDAQ_CM,
    "cboe bzx": UnderlyingMarket.CBOE_BZX,
    "cbobzx": UnderlyingMarket.CBOE_BZX,
    "bzx": UnderlyingMarket.CBOE_BZX,
    "otc": UnderlyingMarket.OTC,
    "otc us": UnderlyingMarket.OTC,
    "otcus": UnderlyingMarket.OTC,
    "otc markets": UnderlyingMarket.OTC,
    "b3": UnderlyingMarket.B3,
    "b3 sa": UnderlyingMarket.B3,
    "xetra": UnderlyingMarket.XETRA,
    "deutsche boerse": UnderlyingMarket.XETRA,
    "lse": UnderlyingMarket.LSE,
    "london stock exchange": UnderlyingMarket.LSE,
    "london": UnderlyingMarket.LSE,
}

#: Raw values that carry no market information at all. These are *expected*
#: (a program can be suspended, an ETF row can omit the column), so they are
#: not treated as data-quality warnings.
_MISSING_MARKETS: Final[frozenset[str]] = frozenset({"", "none", "n/a", "na", "-", "null"})

#: Marks in the free-text "Observaciones Programa" field, lowercased and
#: accent-stripped. ``INACTIVE`` is tested first because ``inhabilitado``
#: *contains* ``habilitado``: matching the active marker first would classify
#: every disabled program as active.
#:
#: The second inactive marker covers the misspelling COMAFI publishes as
#: "Inhablitado" (transposed ``l``), which does not start with "inhabilit".
_INACTIVE_MARKERS: Final[tuple[str, ...]] = ("inhabilit", "inhablit")
_ACTIVE_MARKER: Final[str] = "habilitado"

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
_RATIO_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?P<num>\d+(?:[.,]\d+)?)\s*:\s*(?P<den>\d+(?:[.,]\d+)?)\s*$"
)

#: Decimal places kept for a conversion ratio. Matches the ``NUMERIC(20, 10)``
#: column so an in-memory value and a stored value compare equal.
RATIO_SCALE: Final[int] = 10
_RATIO_QUANTUM: Final[Decimal] = Decimal(1).scaleb(-RATIO_SCALE)


def collapse_whitespace(value: str | None) -> str:
    r"""Collapse runs of whitespace and strip the result.

    Official feeds embed newlines and non-breaking spaces inside values
    (``"20:1\n"``, ``"\xa0 14.700.000\xa0"``). Callers compare the result
    against the vocabulary, so the normalisation has to happen exactly once.
    """
    if not value:
        return ""
    return _WHITESPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()


def strip_accents(value: str) -> str:
    """Return ``value`` with diacritics removed.

    Needed to match source spellings such as ``"Cotizacion"`` regardless of
    whether the source escaped or encoded the accent.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalise_market(raw: str | None) -> UnderlyingMarket:
    """Map a published market string onto :class:`UnderlyingMarket`.

    Returns :attr:`UnderlyingMarket.UNKNOWN` for anything not in the explicit
    alias table, including empty values and the sentinels in
    ``_MISSING_MARKETS``. Callers decide whether an ``UNKNOWN`` is worth a
    warning; the mapping itself never raises.
    """
    candidate = collapse_whitespace(raw).lower()
    if candidate in _MISSING_MARKETS:
        return UnderlyingMarket.UNKNOWN
    return _MARKET_ALIASES.get(candidate, UnderlyingMarket.UNKNOWN)


def is_known_market(raw: str | None) -> bool:
    """Whether ``raw`` resolves to a real venue rather than ``UNKNOWN``."""
    return normalise_market(raw) is not UnderlyingMarket.UNKNOWN


def is_missing_market(raw: str | None) -> bool:
    """Whether ``raw`` is one of the empty placeholders rather than a venue.

    A blank or ``"none"`` market is *expected* - a suspended program or an ETF
    row with no venue column - so it must not be reported as a data-quality
    warning. This is what separates "the source said nothing" from "the source
    said something I do not recognise", and the two must not be conflated: the
    second needs a human, the first does not.
    """
    return collapse_whitespace(raw).lower() in _MISSING_MARKETS


def normalise_program_status(raw: str | None) -> ProgramStatus:
    """Classify the free-text "Observaciones Programa" field.

    The field is a sentence, not a code, and carries footnote markers. Only the
    issuance wording is interpreted; the raw text is stored alongside the
    classification so the interpretation can be audited.
    """
    text = collapse_whitespace(strip_accents(collapse_whitespace(raw)).lower())
    if any(marker in text for marker in _INACTIVE_MARKERS):
        return ProgramStatus.INACTIVE
    if _ACTIVE_MARKER in text:
        return ProgramStatus.ACTIVE
    return ProgramStatus.UNKNOWN


def parse_ratio(raw: str | None) -> Decimal | None:
    """Parse a published conversion ratio into CEDEARs per underlying unit.

    The ratio is a *fraction*, published as ``"N:D"``:

    * ``"60:1"`` - sixty CEDEARs represent one ETF share -> ``60``,
    * ``"1:5"`` - one CEDEAR represents five underlying shares -> ``0.2``.

    Both directions occur in real data, so the value is treated as a fraction
    rather than assumed to be an integer. ``"1 :4"`` (with the stray space the
    source sometimes emits) parses to ``0.25``.

    The result is quantised to :data:`RATIO_SCALE` decimal places so that a
    value recomputed from the source compares exactly equal to the value
    already stored in a ``NUMERIC(20, 10)`` column. Without that, a re-ingest
    would see a spurious difference on every row and open a new ratio period
    each run.

    Returns ``None`` when the value is absent or is not an unambiguous ``N:D``
    pair. Notably ``"15.1"`` yields ``None``: it could be ``15:1`` or ``1.5:1``,
    and guessing would corrupt every future theoretical price.
    """
    candidate = collapse_whitespace(raw).replace("\xa0", " ")
    if not candidate:
        return None
    match = _RATIO_RE.match(candidate)
    if match is None:
        return None
    numerator = Decimal(match.group("num").replace(",", "."))
    denominator = Decimal(match.group("den").replace(",", "."))
    if denominator == 0:
        return None
    return (numerator / denominator).quantize(_RATIO_QUANTUM, rounding=ROUND_HALF_UP)


def format_ratio(ratio: Decimal | None) -> str | None:
    """Render a stored ratio back to the ``"N:D"`` form used by the sources.

    Used by the API so a consumer sees ``"1:5"`` rather than ``"0.2"`` and does
    not have to rediscover the orientation convention.
    """
    if ratio is None:
        return None
    if ratio == ratio.to_integral_value():
        return f"{ratio.to_integral_value()}:1"
    return f"1:{Decimal(1) / ratio:.10f}".rstrip("0").rstrip(".")


def normalise_symbol(raw: str | None) -> str | None:
    r"""Normalise a ticker.

    Tickers arrive with trailing newlines (``"WDC\n"``), non-breaking spaces
    and inconsistent case. Internal spaces are preserved because they are
    meaningful for some listings (``"IWDA LN"``); only surrounding noise and
    case are normalised.
    """
    candidate = collapse_whitespace(raw)
    if not candidate:
        return None
    return candidate.upper()
