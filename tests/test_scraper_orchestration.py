from datetime import date
from pathlib import Path
import pytest
import respx
import httpx
from visa_tracker.db import Database
from visa_tracker.scraper import BulletinScraper, MIN_BULLETIN_MONTH
from visa_tracker.notifier import Notifier

FIXTURES = Path(__file__).parent / "fixtures"


class RecordingSender:
    def __init__(self):
        self.sent = []

    async def __call__(self, text: str):
        self.sent.append(text)


@pytest.mark.asyncio
@respx.mock
async def test_finds_new_bulletin_and_notifies(tmp_path):
    listing_html = (FIXTURES / "listing-page.html").read_text(encoding="utf-8")
    bulletin_html = (FIXTURES / "bulletin-june-2026.html").read_text(encoding="utf-8")

    respx.get(
        "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
    ).mock(return_value=httpx.Response(200, text=listing_html))
    respx.get(url__regex=r".*visa-bulletin-for-june-2026\.html$").mock(
        return_value=httpx.Response(200, text=bulletin_html))
    respx.get(url__regex=r".*visa-bulletin-for-.*\.html$").mock(
        return_value=httpx.Response(200, text=bulletin_html))

    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    sender = RecordingSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    scraper = BulletinScraper(db=db, notifier=notifier)

    result = await scraper.check_for_new_bulletins()
    assert result.new_bulletin_months  # at least one new
    assert "2026-06" in db.all_bulletin_months()


@pytest.mark.asyncio
@respx.mock
async def test_no_new_bulletins_is_noop(tmp_path):
    listing_html = (FIXTURES / "listing-page.html").read_text(encoding="utf-8")
    respx.get(
        "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
    ).mock(return_value=httpx.Response(200, text=listing_html))

    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    # Pre-populate DB with every month present in listing
    bulletin_html = (FIXTURES / "bulletin-june-2026.html").read_text(encoding="utf-8")
    respx.get(url__regex=r".*visa-bulletin-for-.*\.html$").mock(
        return_value=httpx.Response(200, text=bulletin_html))

    sender = RecordingSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    scraper = BulletinScraper(db=db, notifier=notifier)

    # First run: discovers everything
    await scraper.check_for_new_bulletins()
    sender.sent.clear()
    # Second run: nothing new
    result = await scraper.check_for_new_bulletins()
    assert result.new_bulletin_months == []
    assert sender.sent == []


@pytest.mark.asyncio
@respx.mock
async def test_network_error_logs_and_does_not_raise(tmp_path):
    respx.get(
        "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
    ).mock(side_effect=httpx.ConnectError("boom"))

    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    sender = RecordingSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    scraper = BulletinScraper(db=db, notifier=notifier)

    result = await scraper.check_for_new_bulletins()
    assert result.error is not None
    runs = db.recent_scrape_runs(limit=1)
    assert runs[0].status == "error"


@pytest.mark.asyncio
@respx.mock
async def test_detects_bulletin_amendment(tmp_path):
    """If an existing bulletin's dates change on refetch, fire amended-bulletin alert."""
    listing_html = """<html><body><a href="/visa-bulletin-for-june-2026.html">Jun 2026</a></body></html>"""
    # First version of the bulletin
    first_html = (FIXTURES / "bulletin-june-2026.html").read_text(encoding="utf-8")

    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    sender = RecordingSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    scraper = BulletinScraper(db=db, notifier=notifier)

    # First scrape: bulletin is new, gets inserted
    respx.get(
        "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
    ).mock(return_value=httpx.Response(200, text=listing_html))
    respx.get(url__regex=r".*visa-bulletin-for-june-2026\.html$").mock(
        return_value=httpx.Response(200, text=first_html))

    await scraper.check_for_new_bulletins()
    sender.sent.clear()

    # Now simulate an amendment: same month, different parsed dates
    # We do this by directly editing the DB to a "stale" version, then re-scraping.
    from visa_tracker.scraper import ParsedBulletin
    db.insert_bulletin(
        "2026-06",
        ParsedBulletin(
            final_action_date=date(2017, 1, 1),  # different
            dates_for_filing=date(2017, 6, 1),    # different
            raw_final_action="01JAN17",
            raw_dates_filing="01JUN17",
        ),
        source_url="https://example.com/2026-06",
        simulated=False,
    )

    # Re-scrape: should detect amendment (current HTML differs from stale DB row)
    await scraper.check_for_new_bulletins()
    assert any("Bulletin Amended" in m for m in sender.sent)


@pytest.mark.asyncio
@respx.mock
async def test_ignores_bulletins_older_than_min(tmp_path):
    """Old bulletins (pre-2025) should be filtered out, never scraped or alerted."""
    listing_html = """
    <html><body>
    <a href="/visa-bulletin-for-january-2024.html">Jan 2024</a>
    <a href="/visa-bulletin-for-march-2010.html">Mar 2010</a>
    <a href="/visa-bulletin-for-june-2026.html">Jun 2026</a>
    </body></html>
    """
    bulletin_html = (FIXTURES / "bulletin-june-2026.html").read_text(encoding="utf-8")

    respx.get(
        "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
    ).mock(return_value=httpx.Response(200, text=listing_html))
    respx.get(url__regex=r".*visa-bulletin-for-june-2026\.html$").mock(
        return_value=httpx.Response(200, text=bulletin_html))

    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    sender = RecordingSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    scraper = BulletinScraper(db=db, notifier=notifier)

    result = await scraper.check_for_new_bulletins()
    # Only June 2026 should be discovered; the 2024 and 2010 bulletins are below MIN_BULLETIN_MONTH
    assert result.new_bulletin_months == ["2026-06"]
    assert "2024-01" not in db.all_bulletin_months()
    assert "2010-03" not in db.all_bulletin_months()
