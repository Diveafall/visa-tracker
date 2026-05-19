from datetime import date
from pathlib import Path
import pytest
import respx
import httpx
from visa_tracker.db import Database
from visa_tracker.scraper import BulletinScraper
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
