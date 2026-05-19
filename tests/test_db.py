from datetime import date, datetime
import json
from pathlib import Path
import pytest
from visa_tracker.db import Database
from visa_tracker.scraper import ParsedBulletin

SEED_PATH = Path(__file__).resolve().parents[1] / "seed.json"


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_schema_created(db):
    db.init_schema()
    tables = db.list_tables()
    assert set(tables) >= {"bulletins", "scrape_runs", "notifications_sent"}


def test_seed_loads_twelve_rows(db):
    db.init_schema()
    db.load_seed_if_empty(SEED_PATH)
    assert len(db.all_bulletin_months()) == 12
    assert "2026-06" in db.all_bulletin_months()


def test_seed_idempotent(db):
    db.init_schema()
    db.load_seed_if_empty(SEED_PATH)
    db.load_seed_if_empty(SEED_PATH)  # second call no-ops
    assert len(db.all_bulletin_months()) == 12


def test_seed_pre_marks_notifications(db):
    db.init_schema()
    db.load_seed_if_empty(SEED_PATH)
    for kind in ("new_bulletin", "pd_crossed_filing", "pd_crossed_final"):
        assert db.notification_already_sent(kind, "2026-06")


def test_insert_bulletin_and_retrieve(db):
    db.init_schema()
    parsed = ParsedBulletin(
        final_action_date=date(2017, 9, 22),
        dates_for_filing=date(2018, 3, 22),
        raw_final_action="22SEP17",
        raw_dates_filing="22MAR18",
    )
    db.insert_bulletin(
        bulletin_month="2026-06",
        parsed=parsed,
        source_url="https://example.com",
        simulated=False,
    )
    row = db.get_bulletin("2026-06")
    assert row.final_action_date == date(2017, 9, 22)
    assert row.dates_for_filing == date(2018, 3, 22)
    assert row.simulated is False


def test_insert_or_replace_updates_existing(db):
    db.init_schema()
    p1 = ParsedBulletin(date(2017, 9, 22), date(2018, 3, 22), "22SEP17", "22MAR18")
    p2 = ParsedBulletin(date(2017, 10, 1), date(2018, 4, 1), "01OCT17", "01APR18")
    db.insert_bulletin("2026-06", p1, "https://example.com", simulated=False)
    db.insert_bulletin("2026-06", p2, "https://example.com", simulated=False)
    row = db.get_bulletin("2026-06")
    assert row.final_action_date == date(2017, 10, 1)


def test_previous_bulletin_returns_chronological_predecessor(db):
    db.init_schema()
    db.load_seed_if_empty(SEED_PATH)
    prev = db.previous_bulletin("2026-06")
    assert prev.bulletin_month == "2026-05"


def test_log_scrape_run(db):
    db.init_schema()
    db.log_scrape_run(status="ok", detail="found bulletin 2026-07", bulletin_month="2026-07")
    runs = db.recent_scrape_runs(limit=5)
    assert len(runs) == 1
    assert runs[0].status == "ok"


def test_mark_and_check_notification_sent(db):
    db.init_schema()
    assert not db.notification_already_sent("new_bulletin", "2026-07")
    db.mark_notification_sent("new_bulletin", "2026-07")
    assert db.notification_already_sent("new_bulletin", "2026-07")
