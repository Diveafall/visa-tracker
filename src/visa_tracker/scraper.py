from dataclasses import dataclass
from datetime import date
from bs4 import BeautifulSoup, Tag

from visa_tracker.parsing import parse_visa_date


@dataclass(frozen=True)
class ParsedBulletin:
    final_action_date: date | None
    dates_for_filing: date | None
    raw_final_action: str
    raw_dates_filing: str


def parse_bulletin(html: str) -> ParsedBulletin:
    soup = BeautifulSoup(html, "html.parser")
    raw_final = _extract_f2b_all_other(soup, "Final Action Dates")
    raw_filing = _extract_f2b_all_other(soup, "Dates for Filing")
    return ParsedBulletin(
        final_action_date=parse_visa_date(raw_final),
        dates_for_filing=parse_visa_date(raw_filing),
        raw_final_action=raw_final,
        raw_dates_filing=raw_filing,
    )


def _extract_f2b_all_other(soup: BeautifulSoup, chart_label: str) -> str:
    """Find the F2B row, 'All Other' column cell in the table matching chart_label."""
    table = _find_table_for_chart(soup, chart_label)
    if table is None:
        raise ValueError(f"F2B: could not find table for chart {chart_label!r}")
    header_row = table.find("tr")
    if header_row is None:
        raise ValueError(f"No header row in chart {chart_label!r}")
    headers = [_cell_text(c) for c in header_row.find_all(["th", "td"])]
    col_index = next(
        (i for i, h in enumerate(headers) if "All Chargeability" in h or "All Other" in h),
        None,
    )
    if col_index is None:
        raise ValueError(f"Could not find 'All Chargeability' column in {chart_label!r}")

    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        label = _cell_text(cells[0])
        if label.upper().startswith("F2B"):
            if col_index >= len(cells):
                raise ValueError(f"F2B row missing column {col_index} in {chart_label!r}")
            return _cell_text(cells[col_index])
    raise ValueError(f"F2B row not found in chart {chart_label!r}")


def _find_table_for_chart(soup: BeautifulSoup, chart_label: str) -> Tag | None:
    """Return the first table whose preceding headings contain chart_label."""
    label_lower = chart_label.lower()
    for table in soup.find_all("table"):
        if _any_preceding_heading_matches(table, label_lower):
            return table
    return None


def _any_preceding_heading_matches(table: Tag, label_lower: str) -> bool:
    """Scan backwards from table through heading nodes; return True if any matches label_lower."""
    node = table
    # Scan up to 10 preceding heading/paragraph nodes to find the chart heading
    for _ in range(10):
        node = node.find_previous(["h1", "h2", "h3", "h4", "p", "strong", "b"])
        if node is None:
            return False
        text = node.get_text(" ", strip=True)
        if text and label_lower in text.lower():
            return True
    return False


def _cell_text(cell: Tag) -> str:
    return cell.get_text(" ", strip=True)
