r"""COMAFI Custodio Global - the official list of CEDEAR programs it administers.

Source: ``https://www.comafi.com.ar/custodiaglobal/json/apps/getproducts.aspx``

An anonymous JSON endpoint, which makes it the only official source in this
project that is both complete-featured and machine-readable. It publishes, per
program: the BYMA symbol, the conversion ratio, the underlying ISIN, the
underlying ticker, the underlying venue, and whether the program may still issue
and cancel.

**Coverage.** COMAFI administers only the programs it sponsors. It is disjoint
from the Caja de Valores list (ISIN prefixes ``ARDEUT``/``ARBCOM`` versus
``ARCAVA``), not a superset of it, so the ingestion service unions both
providers. See ``docs/data-providers.md``.

**Known defects in the live feed**, all handled here and covered by tests:

* a record's ``name`` can carry a trailing newline (``"WDC\\n"``),
* ``description`` labels appear both as ``<strong>Label:</strong>Value`` and as
  ``<strong>Label</strong>: Value``,
* the venue field sometimes carries the *sector* instead (``"Idustrial
  Gases"`` for ``LIN``), which is left unrecognised rather than guessed,
* one symbol is published twice, once as a stub with no ratio and the wrong
  underlying ticker,
* ratios appear as ``"15.1"`` for ``ASTS``, which is ambiguous between ``15:1``
  and ``1.5:1`` and is therefore refused.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.domain.vocabulary import (
    InstrumentType,
    collapse_whitespace,
    is_known_market,
    is_missing_market,
    normalise_program_status,
    normalise_symbol,
    parse_ratio,
)
from app.providers.base import (
    CedearDataProvider,
    CedearRecord,
    CedearSnapshot,
    ProviderError,
    RejectedRecord,
)
from app.providers.dedupe import resolve_duplicates
from app.providers.http import DEFAULT_USER_AGENT, FetchCallable, HttpFetcher
from app.providers.implementations.html_tables import normalise_key, strip_markup

logger = get_logger(__name__)

PROVIDER_NAME = "comafi"
PRODUCTS_URL = "https://www.comafi.com.ar/custodiaglobal/json/apps/getproducts.aspx"

#: Description labels, reduced through :func:`normalise_key`.
_LABEL_NAME = "NOMBRE"
_LABEL_MARKET = "MERCADODEVALORSUBYACENTE"
_LABEL_UNDERLYING_TICKER = "TICKERENMERCADODEORIGEN"
_LABEL_SECTOR = "INDUSTRIASOSECTOR"
_LABEL_PROGRAM_NOTES = "OBSERVACIONESPROGRAMA"
_LABEL_DIVIDEND_FREQUENCY = "FRECUENCIADEPAGODEDIVIDENDO"

#: ``section`` values mapped onto the platform vocabulary.
_SECTION_TYPES: dict[str, InstrumentType] = {
    "CEDEARSHARES": InstrumentType.STOCK,
    "CEDEARETF": InstrumentType.ETF,
    "CEDEARCORPORATE": InstrumentType.OTHER,
}

_LIST_ITEM_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL | re.IGNORECASE)
_STRONG_RE = re.compile(r"<strong[^>]*>(.*?)</strong>", re.DOTALL | re.IGNORECASE)

#: ISIN: two country letters, nine alphanumerics, one check digit.
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def parse_description(description: str) -> dict[str, str]:
    """Split a product ``description`` HTML blob into its labelled fields.

    COMAFI writes the label colon either inside or outside the ``<strong>``
    element, and both spellings occur in the same feed, so the colon is
    stripped from whichever side it landed on.
    """
    fields: dict[str, str] = {}
    for item in _LIST_ITEM_RE.findall(description):
        strong = _STRONG_RE.search(item)
        if strong is None:
            continue
        label = normalise_key(strong.group(1)).rstrip(":")
        value = strip_markup(item[strong.end() :]).lstrip(":").strip()
        if label and label not in fields:
            fields[label] = value
    return fields


def clean_isin(value: str | None) -> str | None:
    """Return ``value`` if it has the shape of an ISIN, else ``None``.

    The stub ``WDC`` record publishes an empty ``tip`` and a non-ISIN
    ``character``; storing a malformed identifier would later be mistaken for a
    real one when matching sources.
    """
    candidate = collapse_whitespace(value).upper().replace(" ", "")
    if not candidate:
        return None
    return candidate if _ISIN_RE.match(candidate) else None


def _map_section(section: str | None) -> InstrumentType:
    """Map the feed's ``section`` onto :class:`InstrumentType`."""
    key = normalise_key(section or "")
    return _SECTION_TYPES.get(key, InstrumentType.UNKNOWN)


class ComafiCedearProvider(CedearDataProvider):
    """Reads the COMAFI program catalogue."""

    name = PROVIDER_NAME

    def __init__(self, fetch: FetchCallable | None = None) -> None:
        """Inject a fetcher; tests pass one that serves a recorded fixture."""
        self._fetch: FetchCallable = fetch or HttpFetcher(user_agent=DEFAULT_USER_AGENT)

    @property
    def source_url(self) -> str:
        """The JSON endpoint."""
        return PRODUCTS_URL

    def fetch(self) -> CedearSnapshot:
        """Fetch and parse the catalogue.

        Raises:
            ProviderError: if the endpoint is unreachable or the payload is not
                the expected shape. Per-record problems become rejections.
        """
        payload = self._load_payload()
        if not isinstance(payload, dict) or not isinstance(payload.get("products"), list):
            raise ProviderError(
                f"{PRODUCTS_URL} did not return a 'products' array; the endpoint changed shape"
            )

        records: list[CedearRecord] = []
        rejected: list[RejectedRecord] = []
        warnings: list[str] = []

        for position, product in enumerate(payload["products"]):
            if not isinstance(product, dict):
                rejected.append(
                    RejectedRecord(
                        provider=self.name,
                        reason="malformed_product",
                        detail=f"entry at position {position} is not an object",
                    )
                )
                continue
            record, product_rejections, product_warnings = self._parse_product(product, position)
            warnings.extend(product_warnings)
            rejected.extend(product_rejections)
            if record is not None:
                records.append(record)

        kept, duplicate_rejections = resolve_duplicates(self.name, records)
        rejected.extend(duplicate_rejections)

        logger.info(
            "provider.fetched",
            provider=self.name,
            parsed=len(kept),
            rejected=len(rejected),
            warnings=len(warnings),
        )

        return CedearSnapshot(
            provider=self.name,
            source_url=PRODUCTS_URL,
            fetched_at=datetime.now(UTC),
            records=tuple(kept),
            rejected=tuple(rejected),
            warnings=tuple(warnings),
        )

    def _load_payload(self) -> Any:
        """Fetch and decode the endpoint body.

        Raises:
            ProviderError: if the body is not JSON. An HTML error page served
                with a 200 status is the common case, and it must abort the run
                rather than escape as a :class:`json.JSONDecodeError`, which
                :func:`app.ingestion.service.fetch_and_ingest` does not treat as
                a provider failure and would therefore not suppress
                deactivations.
        """
        body = self._fetch(PRODUCTS_URL)
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError(f"{PRODUCTS_URL} did not return JSON: {exc}") from exc

    def _parse_product(
        self, product: dict[str, Any], position: int
    ) -> tuple[CedearRecord | None, list[RejectedRecord], list[str]]:
        """Convert one product object into a record.

        Returns:
            The record, plus any rejection and any warning raised while
            parsing it. ``None`` is returned only when the product cannot
            identify an instrument at all.
        """
        warnings: list[str] = []
        rejected: list[RejectedRecord] = []

        symbol = normalise_symbol(str(product.get("name") or ""))
        if symbol is None:
            rejected.append(
                RejectedRecord(
                    provider=self.name,
                    reason="missing_symbol",
                    detail=f"product at position {position} has no usable name",
                    raw={"position": position},
                )
            )
            return None, rejected, warnings

        description = str(product.get("description") or "")
        fields = parse_description(description)

        ratio_raw = product.get("character")
        ratio = parse_ratio(ratio_raw)
        if ratio is None and collapse_whitespace(ratio_raw):
            # Present but ambiguous, e.g. "15.1". The instrument is real and
            # is kept, but it has no usable ratio, so downstream theoretical
            # pricing must refuse rather than invent one.
            warnings.append(
                f"{symbol}: ratio {collapse_whitespace(ratio_raw)!r} is not an unambiguous "
                "'N:D' pair; stored without a ratio"
            )

        market_raw = fields.get(_LABEL_MARKET) or None
        if market_raw and not is_missing_market(market_raw) and not is_known_market(market_raw):
            # A placeholder such as "none" means the source said nothing, which
            # needs no human attention. A real word we do not recognise is a
            # different problem: it may be a typo, a sector, or a venue we have
            # not mapped, and it needs one.
            warnings.append(
                f"{symbol}: unrecognised underlying market {market_raw!r}; "
                "stored as UNKNOWN rather than guessed"
            )

        underlying_ticker = normalise_symbol(fields.get(_LABEL_UNDERLYING_TICKER))
        instrument_type = _map_section(str(product.get("section") or ""))
        if instrument_type is InstrumentType.UNKNOWN:
            warnings.append(f"{symbol}: unrecognised section {product.get('section')!r}")

        extra: dict[str, Any] = {
            "comafi_id": product.get("id"),
            "sector": fields.get(_LABEL_SECTOR) or None,
            "share_class": collapse_whitespace(str(product.get("keys") or "")) or None,
            "dividend_frequency": fields.get(_LABEL_DIVIDEND_FREQUENCY) or None,
            "country": next(
                (
                    entry.get("category")
                    for entry in product.get("categories", [])
                    if isinstance(entry, dict)
                ),
                None,
            ),
            "max_amount_raw": collapse_whitespace(str(product.get("price") or "")) or None,
            "clearing_code": collapse_whitespace(str(product.get("code") or "")) or None,
            "ratio_raw": collapse_whitespace(ratio_raw) or None,
        }

        record = CedearRecord(
            symbol=symbol,
            custodian=PROVIDER_NAME,
            source_ref=collapse_whitespace(str(product.get("link") or "")) or None,
            name=strip_markup(str(product.get("summary") or description)) or None,
            instrument_type=instrument_type,
            underlying_symbol=underlying_ticker,
            underlying_name=fields.get(_LABEL_NAME) or None,
            underlying_market_raw=market_raw,
            underlying_isin=clean_isin(product.get("tech")),
            ratio=ratio,
            isin=clean_isin(product.get("tip")),
            program_status=normalise_program_status(fields.get(_LABEL_PROGRAM_NOTES)),
            program_status_raw=fields.get(_LABEL_PROGRAM_NOTES) or None,
            extra=extra,
        )
        return record, rejected, warnings
