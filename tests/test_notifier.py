from datetime import date, datetime
import pytest
from visa_tracker.db import Database, BulletinRow
from visa_tracker.notifier import Notifier, render_new_bulletin_message, render_pd_crossed_message
from visa_tracker.scraper import ParsedBulletin


def test_fmt_date_renders_current_sentinel():
    from visa_tracker.notifier import _fmt_date
    from visa_tracker.parsing import CURRENT_SENTINEL
    assert _fmt_date(CURRENT_SENTINEL) == "C (Current)"
    assert _fmt_date(None) == "U (unavailable)"
    assert _fmt_date(date(2018, 7, 3)) == "03-JUL-2018"


def _row(month: str, final: date, filing: date) -> BulletinRow:
    return BulletinRow(
        bulletin_month=month, final_action_date=final, dates_for_filing=filing,
        source_url=f"https://example.com/{month}", raw_final_action="x",
        raw_dates_filing="x", fetched_at=datetime(2026, 1, 1), simulated=False,
    )


def test_render_new_bulletin_includes_both_dates():
    new = _row("2026-07", date(2017, 10, 22), date(2018, 4, 15))
    prev = _row("2026-06", date(2017, 9, 22), date(2018, 3, 22))
    msg = render_new_bulletin_message(new, prev, priority_date=date(2018, 7, 3))
    assert "July 2026" in msg
    assert "22-OCT-2017" in msg
    assert "15-APR-2018" in msg
    assert "+30" in msg or "+30 days" in msg
    assert "03-JUL-2018" in msg


def test_render_pd_crossed_message():
    new = _row("2026-07", date(2018, 8, 1), date(2018, 9, 1))
    msg = render_pd_crossed_message(new, chart="final_action", priority_date=date(2018, 7, 3))
    assert "PRIORITY DATE CURRENT" in msg
    assert "Final Action" in msg
    assert "01-AUG-2018" in msg


class FakeSender:
    def __init__(self, fail_with: Exception | None = None):
        self.sent = []
        self.fail_with = fail_with

    async def __call__(self, text: str) -> None:
        if self.fail_with:
            raise self.fail_with
        self.sent.append(text)


@pytest.mark.asyncio
async def test_notifier_skips_if_already_sent(tmp_path):
    db = Database(str(tmp_path / "n.db"))
    db.init_schema()
    db.mark_notification_sent("new_bulletin", "2026-07")
    sender = FakeSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    new = _row("2026-07", date(2017, 10, 22), date(2018, 4, 15))
    await notifier.handle_new_bulletin(new, prev=None)
    assert sender.sent == []


@pytest.mark.asyncio
async def test_notifier_sends_and_marks(tmp_path):
    db = Database(str(tmp_path / "n.db"))
    db.init_schema()
    sender = FakeSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    new = _row("2026-07", date(2017, 10, 22), date(2018, 4, 15))
    prev = _row("2026-06", date(2017, 9, 22), date(2018, 3, 22))
    await notifier.handle_new_bulletin(new, prev=prev)
    assert len(sender.sent) == 1
    assert "July 2026" in sender.sent[0]
    assert db.notification_already_sent("new_bulletin", "2026-07")


@pytest.mark.asyncio
async def test_notifier_does_not_mark_on_send_failure(tmp_path):
    db = Database(str(tmp_path / "n.db"))
    db.init_schema()
    sender = FakeSender(fail_with=RuntimeError("network down"))
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    new = _row("2026-07", date(2017, 10, 22), date(2018, 4, 15))
    with pytest.raises(RuntimeError):
        await notifier.handle_new_bulletin(new, prev=None)
    assert not db.notification_already_sent("new_bulletin", "2026-07")


@pytest.mark.asyncio
async def test_notifier_marks_sent_on_4xx_to_stop_retry(tmp_path):
    import httpx
    db = Database(str(tmp_path / "n.db"))
    db.init_schema()

    fake_response = httpx.Response(401, text="Unauthorized")
    fake_request = httpx.Request("POST", "https://api.telegram.org/")
    err = httpx.HTTPStatusError("4xx", request=fake_request, response=fake_response)
    sender = FakeSender(fail_with=err)
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    new = _row("2026-07", date(2017, 10, 22), date(2018, 4, 15))
    # Should not raise — 4xx is terminal, marks sent
    await notifier.handle_new_bulletin(new, prev=None)
    assert db.notification_already_sent("new_bulletin", "2026-07")


@pytest.mark.asyncio
async def test_notifier_does_not_mark_on_5xx(tmp_path):
    import httpx
    db = Database(str(tmp_path / "n.db"))
    db.init_schema()

    fake_response = httpx.Response(500, text="Server Error")
    fake_request = httpx.Request("POST", "https://api.telegram.org/")
    err = httpx.HTTPStatusError("5xx", request=fake_request, response=fake_response)
    sender = FakeSender(fail_with=err)
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    new = _row("2026-07", date(2017, 10, 22), date(2018, 4, 15))
    with pytest.raises(httpx.HTTPStatusError):
        await notifier.handle_new_bulletin(new, prev=None)
    assert not db.notification_already_sent("new_bulletin", "2026-07")


@pytest.mark.asyncio
async def test_notifier_fires_pd_crossed_when_pd_crosses_filing(tmp_path):
    db = Database(str(tmp_path / "n.db"))
    db.init_schema()
    sender = FakeSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    prev = _row("2026-06", date(2017, 9, 22), date(2018, 3, 22))
    new = _row("2026-07", date(2017, 10, 22), date(2018, 8, 1))  # filing crosses 2018-07-03
    await notifier.handle_new_bulletin(new, prev=prev)
    assert any("PRIORITY DATE CURRENT" in m and "Filing" in m for m in sender.sent)
