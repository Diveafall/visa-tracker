from datetime import date
from pathlib import Path
import pytest
from visa_tracker.scraper import parse_bulletin

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("filename, expected_final, expected_filing", [
    ("bulletin-june-2026.html",     date(2017, 9, 22), date(2018, 3, 22)),
    ("bulletin-april-2026.html",    date(2017, 5, 22), date(2017, 8, 8)),
    ("bulletin-november-2025.html", date(2016, 12, 1), date(2017, 3, 8)),
])
def test_parse_bulletin_extracts_f2b_all_other(filename, expected_final, expected_filing):
    html = (FIXTURES / filename).read_text(encoding="utf-8")
    parsed = parse_bulletin(html)
    assert parsed.final_action_date == expected_final
    assert parsed.dates_for_filing == expected_filing


def test_parse_bulletin_preserves_raw_strings():
    html = (FIXTURES / "bulletin-june-2026.html").read_text(encoding="utf-8")
    parsed = parse_bulletin(html)
    assert parsed.raw_final_action == "22SEP17"
    assert parsed.raw_dates_filing == "22MAR18"


def test_parse_bulletin_missing_f2b_raises():
    with pytest.raises(ValueError, match="F2B"):
        parse_bulletin("<html><body>no tables here</body></html>")


from visa_tracker.scraper import list_available_bulletins, BulletinLink


def test_list_available_bulletins_returns_recent_months():
    html = (FIXTURES / "listing-page.html").read_text(encoding="utf-8")
    links = list_available_bulletins(html, base_url="https://travel.state.gov/")
    months = [b.bulletin_month for b in links]
    # June 2026 is the upcoming bulletin at fixture-capture time
    assert "2026-06" in months
    assert "2026-05" in months
    assert "2026-04" in months
    # newest-first ordering
    assert months == sorted(months, reverse=True)


def test_list_available_bulletins_dedupes():
    """'Current' link and FY section list the same URL — should appear once."""
    html = (FIXTURES / "listing-page.html").read_text(encoding="utf-8")
    links = list_available_bulletins(html, base_url="https://travel.state.gov/")
    months = [b.bulletin_month for b in links]
    assert len(months) == len(set(months))


def test_bulletin_link_url_is_absolute():
    html = (FIXTURES / "listing-page.html").read_text(encoding="utf-8")
    links = list_available_bulletins(html, base_url="https://travel.state.gov/")
    for b in links:
        assert b.url.startswith("https://")
