"""Caja de Valores S.A. - the official list of the CEDEAR programs it issues.

Source: ``https://cajadevalores.com.ar/Servicios/Cedears``

The page is server-rendered HTML with no machine-readable alternative, so the
adapter parses the two ``tabla-cedears`` tables: one for ``CEDEAR de ETF`` and
one for ``CEDEAR de Acciones``. Columns are located by header name rather than
by position, because the two tables spell some headers differently
(``ISIN ETF`` versus ``ISIN Acción``) and the headers are line-broken in the
markup.

**Coverage.** This list covers only the programs *issued by Caja de Valores
S.A.* It is disjoint from the COMAFI catalogue (ISIN prefix ``ARCAVA`` versus
``ARDEUT``/``ARBCOM``), not a superset, so the ingestion service unions both
providers. About half the CEDEARs traded on BYMA are sponsored programs issued
by the foreign company itself and appear in neither list; those are reached
through BYMA, whose full-universe list is published only as a PDF. See
``docs/data-providers.md``.

Cell text arrives with nested links, ``<br>``, ``&amp;`` and non-breaking
spaces, all of which are normalised by
:mod:`app.providers.implementations.html_tables`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.domain.vocabulary import (
    InstrumentType,
    collapse_whitespace,
    is_known_market,
    is_missing_market,
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
from app.providers.http import DEFAULT_USER_AGENT, FetchCallable, HttpFetcher, decode_html
from app.providers.implementations.html_tables import HtmlTable, extract_tables, normalise_key

logger = get_logger(__name__)

PROVIDER_NAME = "cajadevalores"
CEDEARS_URL = "https://cajadevalores.com.ar/Servicios/Cedears"
TABLE_CLASS = "tabla-cedears"

#: The first header cell of each table names the kind of program it lists, and
#: is not a data column. Everything else is matched by name.
_TABLE_KINDS: dict[str, InstrumentType] = {
    "CEDEARDEETF": InstrumentType.ETF,
    "CEDEARDEACCIONES": InstrumentType.STOCK,
    "CEDEARDEACCION": InstrumentType.STOCK,
}

_COLUMN_RATIO = ("Ratio CEDEARs / valor subyacente",)
_COLUMN_SYMBOL = ("Símbolo BYMA",)
_COLUMN_UNDERLYING_TICKER = ("Ticker en Mercado de Origen",)
_COLUMN_ISIN = ("ISIN Cedear",)
_COLUMN_UNDERLYING_ISIN = ("ISIN Acción", "ISIN Accion", "ISIN ETF")
_COLUMN_MARKET = ("Mercado de Origen",)
_COLUMN_MAX_AMOUNT = ("Monto máximo", "Monto maximo")
_COLUMN_CLEARING_CODE = ("Código Caja de Valores Cedear", "Codigo Caja de Valores Cedear")

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def clean_isin(value: str | None) -> str | None:
    """Return ``value`` if it has the shape of an ISIN, else ``None``."""
    candidate = collapse_whitespace(value).upper().replace(" ", "")
    if not candidate:
        return None
    return candidate if _ISIN_RE.match(candidate) else None


def _cell(row: list[str], column: int | None) -> str | None:
    """Return a whitespace-collapsed cell value, or ``None`` if out of range.

    Rows are not guaranteed to be rectangular, so an absent column must be a
    miss rather than an ``IndexError``.
    """
    if column is None or column >= len(row):
        return None
    return collapse_whitespace(row[column]) or None


def table_kind(table: HtmlTable) -> InstrumentType | None:
    """Identify which kind of program a table lists, from its first header.

    Returns ``None`` when the header is not recognised, which means the page
    layout changed and the table must not be parsed positionally.
    """
    if not table.headers:
        return None
    return _TABLE_KINDS.get(normalise_key(table.headers[0]))


class CajaDeValoresCedearProvider(CedearDataProvider):
    """Reads the Caja de Valores CEDEAR tables."""

    name = PROVIDER_NAME

    def __init__(self, fetch: FetchCallable | None = None) -> None:
        """Inject a fetcher; tests pass one that serves a recorded fixture."""
        self._fetch: FetchCallable = fetch or HttpFetcher(user_agent=DEFAULT_USER_AGENT)

    @property
    def source_url(self) -> str:
        """The page holding the two tables."""
        return CEDEARS_URL

    def fetch(self) -> CedearSnapshot:
        """Fetch and parse both tables.

        Raises:
            ProviderError: if the page is unreachable, carries no recognisable
                table, or the columns cannot be located.
        """
        document = decode_html(self._fetch(CEDEARS_URL))
        tables = extract_tables(document, TABLE_CLASS)
        if not tables:
            raise ProviderError(
                f"{CEDEARS_URL} contains no '{TABLE_CLASS}' table; the page layout has changed"
            )

        records: list[CedearRecord] = []
        rejected: list[RejectedRecord] = []
        warnings: list[str] = []

        for index, table in enumerate(tables):
            kind = table_kind(table)
            if kind is None:
                first = table.headers[0] if table.headers else "<no header>"
                warnings.append(
                    f"table {index} starts with unrecognised header {first!r}; skipped. "
                    "A new table type may have been added to the page."
                )
                continue
            table_records, table_rejections, table_warnings = self._parse_table(table, kind, index)
            records.extend(table_records)
            rejected.extend(table_rejections)
            warnings.extend(table_warnings)

        if not records:
            raise ProviderError(
                f"{CEDEARS_URL} produced no records from {len(tables)} recognised tables; "
                "refusing to report an empty universe"
            )

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
            source_url=CEDEARS_URL,
            fetched_at=datetime.now(UTC),
            records=tuple(kept),
            rejected=tuple(rejected),
            warnings=tuple(warnings),
        )

    def _parse_table(
        self, table: HtmlTable, kind: InstrumentType, table_index: int
    ) -> tuple[list[CedearRecord], list[RejectedRecord], list[str]]:
        """Convert one table into records.

        Raises:
            ProviderError: if a required column cannot be located, since a
                positional fallback would silently mis-assign ratios.
        """
        warnings: list[str] = []
        rejected: list[RejectedRecord] = []

        ratio_col = table.column_index(*_COLUMN_RATIO)
        symbol_col = table.column_index(*_COLUMN_SYMBOL)
        ticker_col = table.column_index(*_COLUMN_UNDERLYING_TICKER)
        isin_col = table.column_index(*_COLUMN_ISIN)
        underlying_isin_col = table.column_index(*_COLUMN_UNDERLYING_ISIN)
        market_col = table.column_index(*_COLUMN_MARKET)
        max_amount_col = table.column_index(*_COLUMN_MAX_AMOUNT)
        clearing_code_col = table.column_index(*_COLUMN_CLEARING_CODE)

        missing = [
            name
            for name, col in (
                ("ratio", ratio_col),
                ("symbol", symbol_col),
                ("underlying ticker", ticker_col),
                ("market", market_col),
            )
            if col is None
        ]
        if missing:
            raise ProviderError(
                f"table {table_index} is missing the {', '.join(missing)} column(s); "
                f"headers seen: {table.headers}"
            )

        records: list[CedearRecord] = []
        for row in table.rows:
            symbol = normalise_symbol(_cell(row, symbol_col))
            if symbol is None:
                rejected.append(
                    RejectedRecord(
                        provider=self.name,
                        reason="missing_symbol",
                        detail=f"table {table_index} row has no symbol in column {symbol_col}",
                        raw={"row": row},
                    )
                )
                continue

            ratio_raw = _cell(row, ratio_col)
            ratio = parse_ratio(ratio_raw)
            if ratio is None:
                warnings.append(
                    f"{symbol}: no unambiguous conversion ratio in the Caja de Valores table "
                    f"(saw {ratio_raw!r}); stored without a ratio"
                )

            market_raw = _cell(row, market_col)
            if market_raw and not is_missing_market(market_raw) and not is_known_market(market_raw):
                warnings.append(
                    f"{symbol}: unrecognised underlying market {market_raw!r}; "
                    "stored as UNKNOWN rather than guessed"
                )

            records.append(
                CedearRecord(
                    symbol=symbol,
                    custodian=PROVIDER_NAME,
                    source_ref=CEDEARS_URL,
                    name=_cell(row, 0),
                    instrument_type=kind,
                    underlying_symbol=normalise_symbol(_cell(row, ticker_col)),
                    underlying_market_raw=market_raw,
                    underlying_isin=clean_isin(_cell(row, underlying_isin_col)),
                    ratio=ratio,
                    isin=clean_isin(_cell(row, isin_col)),
                    # The page does not state whether a program may still issue
                    # or cancel, so the status stays UNKNOWN rather than being
                    # assumed from the mere presence of the row.
                    extra={
                        "max_amount_raw": _cell(row, max_amount_col),
                        "clearing_code": _cell(row, clearing_code_col),
                        "ratio_raw": ratio_raw,
                    },
                )
            )

        return records, rejected, warnings
