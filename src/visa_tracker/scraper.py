import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING
from urllib.parse import urljoin
import httpx
import logging

from bs4 import BeautifulSoup, Tag

from visa_tracker.parsing import parse_visa_date

if TYPE_CHECKING:
    from visa_tracker.db import Database

log = logging.getLogger(__name__)

LISTING_URL = "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
USER_AGENT = "visa-tracker/1.0 (+personal use)"
HTTP_TIMEOUT = 20.0
MIN_BULLETIN_MONTH = "2025-01"  # ignore older bulletins (parser not designed for them)


@dataclass(frozen=True)
class BulletinLink:
    bulletin_month: str   # 'YYYY-MM'
    url: str


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


@dataclass
class ScrapeResult:
    new_bulletin_months: list[str] = field(default_factory=list)
    error: str | None = None


class BulletinScraper:
    def __init__(self, *, db: "Database", notifier):
        self.db = db
        self.notifier = notifier

    async def check_for_new_bulletins(self) -> ScrapeResult:
        result = ScrapeResult()
        try:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
            ) as client:
                listing_html = (await client.get(LISTING_URL)).text
                available = list_available_bulletins(listing_html, base_url=LISTING_URL)
                known = set(self.db.all_bulletin_months())
                new_links = [
                    b for b in available
                    if b.bulletin_month not in known
                    and b.bulletin_month >= MIN_BULLETIN_MONTH
                ]
                # process oldest-first so notifications fire in order
                for link in sorted(new_links, key=lambda b: b.bulletin_month):
                    bulletin_html = (await client.get(link.url)).text
                    try:
                        parsed = parse_bulletin(bulletin_html)
                    except ValueError as e:
                        log.exception("parser failed for %s", link.url)
                        self.db.log_scrape_run(status="error",
                                               detail=f"parser_broken: {e}",
                                               bulletin_month=link.bulletin_month)
                        await self.notifier.handle_parser_broken(
                            link.bulletin_month, str(e))
                        continue
                    prev = self.db.previous_bulletin(link.bulletin_month)
                    self.db.insert_bulletin(link.bulletin_month, parsed,
                                            link.url, simulated=False)
                    new_row = self.db.get_bulletin(link.bulletin_month)
                    await self.notifier.handle_new_bulletin(new_row, prev)
                    result.new_bulletin_months.append(link.bulletin_month)
                    self.db.log_scrape_run(status="new_bulletin",
                                           bulletin_month=link.bulletin_month)
            if not result.new_bulletin_months:
                self.db.log_scrape_run(status="no_change")
        except Exception as e:
            log.exception("scrape failed")
            result.error = str(e)
            self.db.log_scrape_run(status="error", detail=str(e))
        return result


_HREF_RE = re.compile(r"visa-bulletin-for-([a-z]+)-(\d{4})\.html", re.IGNORECASE)
_MONTH_NUM = {m.lower(): i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june",
     "july", "august", "september", "october", "november", "december"])}


def list_available_bulletins(html: str, *, base_url: str) -> list[BulletinLink]:
    soup = BeautifulSoup(html, "html.parser")
    seen: dict[str, BulletinLink] = {}
    for a in soup.find_all("a", href=True):
        match = _HREF_RE.search(a["href"])
        if not match:
            continue
        month_word, year_str = match.group(1).lower(), match.group(2)
        month_num = _MONTH_NUM.get(month_word)
        if month_num is None:
            continue
        bulletin_month = f"{year_str}-{month_num:02d}"
        if bulletin_month in seen:
            continue
        seen[bulletin_month] = BulletinLink(
            bulletin_month=bulletin_month,
            url=urljoin(base_url, a["href"]),
        )
    return sorted(seen.values(), key=lambda b: b.bulletin_month, reverse=True)
