from datetime import date
from visa_tracker.projection import compute_projection, Projection
from visa_tracker.db import BulletinRow
from datetime import datetime


def _row(month: str, final: date, filing: date) -> BulletinRow:
    return BulletinRow(
        bulletin_month=month, final_action_date=final, dates_for_filing=filing,
        source_url="x", raw_final_action="x", raw_dates_filing="x",
        fetched_at=datetime(2026, 1, 1), simulated=False,
    )


def test_projection_returns_none_if_pd_already_current():
    history = [_row("2026-05", date(2019, 1, 1), date(2019, 6, 1))]
    proj = compute_projection(history, priority_date=date(2018, 7, 3))
    assert proj.final_action_eta_range is None
    assert proj.filing_eta_range is None
    assert proj.pd_is_current_final is True
    assert proj.pd_is_current_filing is True


def test_projection_uses_recent_pace():
    history = [
        _row("2026-04", date(2017, 5, 22), date(2017, 8, 8)),
        _row("2026-05", date(2017, 5, 22), date(2018, 1, 1)),
        _row("2026-06", date(2017, 9, 22), date(2018, 3, 22)),
    ]
    proj = compute_projection(history, priority_date=date(2018, 7, 3))
    # Filing chart has 7+ months to cover, recent pace ~60 days/bulletin
    # Final action has 9+ months to cover at ~60 days/bulletin
    assert proj.filing_eta_range is not None
    assert proj.final_action_eta_range is not None
    earliest, latest = proj.filing_eta_range
    assert earliest <= latest
    # Should be within the next two years roughly
    assert earliest.year in (2026, 2027, 2028)


def test_projection_returns_no_eta_if_pace_zero_or_negative():
    history = [
        _row("2026-04", date(2016, 12, 1), date(2017, 3, 15)),
        _row("2026-05", date(2016, 12, 1), date(2017, 3, 15)),
        _row("2026-06", date(2016, 12, 1), date(2017, 3, 15)),
    ]
    proj = compute_projection(history, priority_date=date(2018, 7, 3))
    assert proj.final_action_eta_range is None
    assert proj.filing_eta_range is None
    assert proj.pd_is_current_final is False
