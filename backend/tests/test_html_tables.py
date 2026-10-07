"""HTML table extraction tests.

The Caja de Valores page is server-rendered with no machine-readable
alternative, so this parser is load-bearing. Each test pins one real defect of
the page: line-broken headers, nested markup inside cells, non-breaking spaces,
and a table class that must be filtered on.
"""

from __future__ import annotations

import pytest

from app.providers.implementations.html_tables import (
    extract_tables,
    normalise_key,
    strip_markup,
)

pytestmark = pytest.mark.unit

SIMPLE_PAGE = """
<html><body>
<table class="otra">
  <tr><th>A</th><th>B</th></tr>
  <tr><td>1</td><td>2</td></tr>
</table>
<table class="tabla-cedears">
  <thead>
    <tr><th>CEDEAR de ETF</th><th>Símbolo<br>BYMA</th></tr>
  </thead>
  <tbody>
    <tr><th><a href="/x">SPDR S&amp;P 500</a></th><td>SPY</td></tr>
    <tr><th>ISHARES&nbsp; MSCI EM</th><td>EEM</td></tr>
  </tbody>
</table>
</body></html>
"""


class TestStripMarkup:
    """Cell text must arrive as visible, whitespace-normalised text."""

    def test_entity_is_decoded(self) -> None:
        """``S&amp;P`` is a single company name, not a separator."""
        assert strip_markup("<td>S&amp;P 500</td>") == "S&P 500"

    def test_nested_link_keeps_its_text(self) -> None:
        """The visible text of a linked cell is the text, not the href."""
        assert strip_markup('<th><a href="/x">SPDR S&amp;P 500</a></th>') == "SPDR S&P 500"

    def test_line_break_becomes_a_space(self) -> None:
        """Deleting ``<br>`` would concatenate two words into one token."""
        assert strip_markup("SPDR<br>S&P 500") == "SPDR S&P 500"

    def test_nbsp_becomes_a_plain_space(self) -> None:
        """Non-breaking spaces must not survive into a comparison key."""
        assert strip_markup("ISHARES\xa0 MSCI") == "ISHARES MSCI"

    def test_script_and_style_are_dropped(self) -> None:
        """Inline styling text is not page content."""
        html = "<style>.a{color:red}</style><td>SPY</td><script>var x=1</script>"
        assert strip_markup(html) == "SPY"

    def test_entities_are_decoded_inside_suppressed_content(self) -> None:
        """Suppression must not leave a decoded entity fragment behind."""
        assert "var" not in strip_markup("<script>var x = '&amp;';</script>")

    def test_whitespace_runs_collapse(self) -> None:
        """Markup-generated indentation is not information."""
        assert strip_markup("<td>\n   SPY \n</td>") == "SPY"


class TestNormaliseKey:
    """Header lookup keys ignore whitespace, case and accents."""

    def test_line_break_does_not_change_the_key(self) -> None:
        """A break inside a header must not create a different column."""
        assert normalise_key("Símbolo<br>BYMA") == normalise_key("Simbolo BYMA")

    def test_accent_is_removed(self) -> None:
        """``Símbolo`` and ``Simbolo`` must resolve to the same column."""
        assert normalise_key("Símbolo") == "SIMBOLO"

    def test_case_is_removed(self) -> None:
        """Lookup is case-insensitive by construction."""
        assert normalise_key("ratio") == normalise_key("RATIO") == "RATIO"


class TestExtractTables:
    """Only tables carrying the requested class are returned."""

    def test_filters_by_class(self) -> None:
        """The page contains other tables that must not be parsed."""
        tables = extract_tables(SIMPLE_PAGE, "tabla-cedears")
        assert len(tables) == 1
        assert tables[0].headers[0] == "CEDEAR de ETF"

    def test_first_row_is_the_header_when_there_is_no_thead(self) -> None:
        """Some published tables omit ``<thead>`` entirely."""
        page = """
        <table class="t">
          <tr><th>A</th><th>B</th></tr>
          <tr><td>1</td><td>2</td></tr>
        </table>
        """
        table = extract_tables(page, "t")[0]
        assert table.headers == ["A", "B"]
        assert table.rows == [["1", "2"]]

    def test_rows_are_collected_in_document_order(self) -> None:
        """Row order is the only ordering the source provides."""
        table = extract_tables(SIMPLE_PAGE, "tabla-cedears")[0]
        assert [row[1] for row in table.rows] == ["SPY", "EEM"]

    def test_cell_text_is_normalised(self) -> None:
        """Cells go through the same normalisation as headers."""
        table = extract_tables(SIMPLE_PAGE, "tabla-cedears")[0]
        assert table.rows[0][0] == "SPDR S&P 500"
        assert table.rows[1][0] == "ISHARES MSCI EM"

    def test_nested_table_does_not_end_the_outer_one(self) -> None:
        """A table inside a cell must not terminate the outer table."""
        page = """
        <table class="t">
          <tr><th>A</th><th>B</th></tr>
          <tr><td><table class="inner"><tr><td>x</td></tr></table></td><td>2</td></tr>
          <tr><td>3</td><td>4</td></tr>
        </table>
        """
        table = extract_tables(page, "t")[0]
        assert table.rows[-1] == ["3", "4"]

    def test_no_matching_table_yields_an_empty_list(self) -> None:
        """An empty result means the layout changed, which callers treat as an error."""
        assert extract_tables(SIMPLE_PAGE, "no-such-class") == []


class TestHtmlTable:
    """Column lookup by name, with a miss rather than a wrong positional guess."""

    def test_column_index_by_name(self) -> None:
        """Headers are located by name, not by position."""
        table = extract_tables(SIMPLE_PAGE, "tabla-cedears")[0]
        assert table.column_index("Símbolo BYMA") == 1

    def test_line_broken_header_still_matches(self) -> None:
        """A break inside a published header is not a layout change."""
        table = extract_tables(SIMPLE_PAGE, "tabla-cedears")[0]
        assert table.column_index("Símbolo BYMA") == table.column_index("Simbolo Byma")

    def test_first_matching_candidate_wins(self) -> None:
        """The two tables spell one header differently; candidates are ordered."""
        table = extract_tables(SIMPLE_PAGE, "tabla-cedears")[0]
        assert table.column_index("No Such Column", "Símbolo BYMA") == 1

    def test_absent_header_returns_none(self) -> None:
        """A miss must be distinguishable from column zero.

        The Caja de Valores adapter relies on this to detect a renamed column
        and refuse, instead of silently reading the wrong cell.
        """
        table = extract_tables(SIMPLE_PAGE, "tabla-cedears")[0]
        assert table.column_index("Ratio CEDEARs / valor subyacente") is None

    def test_cells_maps_headers_onto_values(self) -> None:
        """Header-to-value mapping skips positions without a header."""
        table = extract_tables(SIMPLE_PAGE, "tabla-cedears")[0]
        assert table.cells(table.rows[0])["SIMBOLOBYMA"] == "SPY"
