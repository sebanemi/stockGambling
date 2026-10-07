"""Minimal HTML table and text extraction for metadata adapters.

The official sources publish their data as server-rendered HTML with no
machine-readable alternative, so the adapters need a parser. Rather than add a
dependency, this module implements the small, testable subset of parsing the
adapters actually use, on top of the standard library's :mod:`html.parser`.

Two details of the real pages drive the design:

* Header cells are line-broken, so ``"Ticker en Mercado<br>de Origen"`` must
  match a lookup key regardless of where the break falls. Keys are compared
  with all whitespace removed.
* Cells contain nested markup, non-breaking spaces, ``&amp;`` and links.
  ``strip_markup`` therefore replaces block boundaries with a space rather
  than deleting tags, otherwise ``"S&amp;P 500"`` and ``"INVESCO QQQ"``
  silently concatenate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from app.domain.vocabulary import collapse_whitespace, strip_accents

_CELL_TAGS = frozenset({"td", "th"})
_ROW_TAG = "tr"
_TABLE_TAG = "table"
_VOID_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link"})
_SKIPPED_CONTENT_TAGS = frozenset({"script", "style"})
_WHITESPACE_RE = re.compile(r"\s+")


def strip_markup(fragment: str) -> str:
    """Return the visible text of an HTML fragment, whitespace-normalised.

    Comments, ``<script>`` and ``<style>`` contents are dropped, block-level
    tags become a single space, and character references are decoded.
    """
    parser = _TextExtractor()
    parser.feed(fragment)
    parser.close()
    return collapse_whitespace(parser.text())


def normalise_key(value: str) -> str:
    """Reduce a label to a comparison key: no whitespace, no accents, uppercase.

    Used for header lookup so that a line break or an accented character in a
    published column name cannot break a mapping.
    """
    return _WHITESPACE_RE.sub("", strip_accents(strip_markup(value))).upper()


@dataclass(slots=True)
class HtmlTable:
    """A parsed HTML table: a header row plus the body rows."""

    headers: list[str]
    rows: list[list[str]]

    def column_index(self, *candidates: str) -> int | None:
        """Index of the first header matching any of ``candidates``.

        Candidates are matched through :func:`normalise_key`, so
        ``column_index("Ratio CEDEARs / valor subyacente")`` succeeds whether or
        not the page breaks that label across two lines. Returns ``None`` when
        no candidate matches, which the adapters treat as a layout change.
        """
        keys = [normalise_key(header) for header in self.headers]
        for candidate in candidates:
            wanted = normalise_key(candidate)
            if wanted in keys:
                return keys.index(wanted)
        return None

    def cells(self, row: list[str]) -> dict[str, str]:
        """Map a row onto its header names, skipping unlabelled positions."""
        return {
            normalise_key(header): value for header, value in zip(self.headers, row, strict=False)
        }


@dataclass(slots=True)
class _TableCollector:
    """Accumulator for the table currently being parsed."""

    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    current_row: list[str] | None = None
    current_cell: list[str] | None = None
    in_head: bool = False
    seen_head: bool = False


class _TextExtractor(HTMLParser):
    """Collect visible text, treating block boundaries as whitespace."""

    def __init__(self) -> None:
        """Initialise the collector."""
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._suppress = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
        """Insert a separator for block boundaries; suppress script and style.

        ``attrs`` is part of the :class:`html.parser.HTMLParser` contract and
        this handler has nothing to read from it.
        """
        if tag in _SKIPPED_CONTENT_TAGS:
            self._suppress += 1
        elif tag in _VOID_TAGS or tag in {"p", "div", "li", "ul", "ol", "tr"}:
            self._parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle self-closing tags such as ``<br/>``."""
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        """Stop suppressing once the skipped element closes."""
        if tag in _SKIPPED_CONTENT_TAGS and self._suppress > 0:
            self._suppress -= 1
        elif tag in _VOID_TAGS or tag in {"p", "div", "li", "ul", "ol", "tr"}:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        """Append visible character data."""
        if self._suppress == 0:
            self._parts.append(data)

    def text(self) -> str:
        """The collected text."""
        return "".join(self._parts)


class _TableParser(HTMLParser):
    """Extract every table whose ``class`` attribute contains ``wanted_class``.

    Nested tables are tracked with a depth counter so that a table inside a
    cell does not terminate the outer one.
    """

    def __init__(self, wanted_class: str) -> None:
        """Configure the class filter."""
        super().__init__(convert_charrefs=True)
        self._wanted = wanted_class
        self._depth = 0
        self._active_depth = 0
        self._collector: _TableCollector | None = None
        self.tables: list[HtmlTable] = []

    def _classes(self, attrs: list[tuple[str, str | None]]) -> set[str]:
        raw = dict(attrs).get("class") or ""
        return {token for token in raw.split() if token}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open tables, rows and cells."""
        if tag == _TABLE_TAG:
            self._depth += 1
            if self._collector is None and self._wanted in self._classes(attrs):
                self._collector = _TableCollector()
                self._active_depth = self._depth
            return

        if self._collector is None or self._depth != self._active_depth:
            return

        if tag == "thead":
            self._collector.in_head = True
        elif tag == _ROW_TAG:
            self._collector.current_row = []
        elif tag in _CELL_TAGS:
            self._collector.current_cell = []
        elif self._collector.current_cell is not None and (
            tag in _VOID_TAGS or tag in {"p", "div", "li", "ul", "ol"}
        ):
            self._collector.current_cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        """Close cells, rows, head sections and tables."""
        if tag == _TABLE_TAG:
            if self._collector is not None and self._depth == self._active_depth:
                self._finish()
            self._depth = max(0, self._depth - 1)
            return

        if self._collector is None or self._depth != self._active_depth:
            return

        if tag == "thead":
            self._collector.in_head = False
        elif tag in _CELL_TAGS and self._collector.current_cell is not None:
            value = collapse_whitespace("".join(self._collector.current_cell))
            if self._collector.current_row is not None:
                self._collector.current_row.append(value)
            self._collector.current_cell = None
        elif tag == _ROW_TAG and self._collector.current_row is not None:
            row = self._collector.current_row
            self._collector.current_row = None
            if not row:
                return
            if self._collector.in_head and not self._collector.seen_head:
                self._collector.headers = row
                self._collector.seen_head = True
            elif self._collector.seen_head:
                self._collector.rows.append(row)
            else:
                # No <thead>: the first row is the header by convention.
                self._collector.headers = row
                self._collector.seen_head = True

    def handle_data(self, data: str) -> None:
        """Accumulate visible text into the open cell."""
        if self._collector is None or self._depth != self._active_depth:
            return
        if self._collector.current_cell is not None:
            self._collector.current_cell.append(data)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
        """Handle self-closing tags inside cells, inserting a separator.

        ``<br/>`` inside a cell must become a space, otherwise
        ``"SPDR<br/>S&P 500"`` concatenates into ``"SPDRS&P 500"``.
        """
        if (
            self._collector is not None
            and self._collector.current_cell is not None
            and tag in _VOID_TAGS
        ):
            self._collector.current_cell.append(" ")

    def _finish(self) -> None:
        """Publish the collected table."""
        collector = self._collector
        self._collector = None
        if collector is not None and collector.headers:
            self.tables.append(HtmlTable(headers=collector.headers, rows=collector.rows))


def extract_tables(document: str, css_class: str) -> list[HtmlTable]:
    """Return every table in ``document`` carrying ``css_class``.

    Args:
        document: The full HTML page.
        css_class: A single class name to filter on, e.g. ``"tabla-cedears"``.

    Returns:
        The matching tables in document order. An empty list means the layout
        has changed, which callers must treat as an error rather than as an
        empty universe.
    """
    parser = _TableParser(css_class)
    parser.feed(document)
    parser.close()
    return parser.tables
