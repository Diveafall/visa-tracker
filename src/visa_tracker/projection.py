from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta

from visa_tracker.db import BulletinRow
from visa_tracker.parsing import CURRENT_SENTINEL


@dataclass(frozen=True)
class Projection:
    pd_is_current_final: bool
    pd_is_current_filing: bool
    final_action_eta_range: tuple[date, date] | None     # (earliest, latest) projection
    filing_eta_range: tuple[date, date] | None
    days_per_month_final_recent: float
    days_per_month_final_year: float
    days_per_month_filing_recent: float
    days_per_month_filing_year: float


def compute_projection(history: list[BulletinRow], priority_date: date) -> Projection:
    real = [b for b in history if not b.simulated]
    real = sorted(real, key=lambda b: b.bulletin_month)
    latest = real[-1] if real else None

    pd_current_final = (
        latest is not None and latest.final_action_date is not None
        and priority_date <= latest.final_action_date
    )
    pd_current_filing = (
        latest is not None and latest.dates_for_filing is not None
        and priority_date <= latest.dates_for_filing
    )

    recent_3 = real[-3:]
    last_12 = real[-12:]
    pace_final_recent = _avg_pace_days_per_month(recent_3, lambda b: b.final_action_date)
    pace_final_year = _avg_pace_days_per_month(last_12, lambda b: b.final_action_date)
    pace_filing_recent = _avg_pace_days_per_month(recent_3, lambda b: b.dates_for_filing)
    pace_filing_year = _avg_pace_days_per_month(last_12, lambda b: b.dates_for_filing)

    return Projection(
        pd_is_current_final=pd_current_final,
        pd_is_current_filing=pd_current_filing,
        final_action_eta_range=_eta_range(
            latest.final_action_date if latest else None, priority_date,
            pace_final_recent, pace_final_year, pd_current_final),
        filing_eta_range=_eta_range(
            latest.dates_for_filing if latest else None, priority_date,
            pace_filing_recent, pace_filing_year, pd_current_filing),
        days_per_month_final_recent=pace_final_recent,
        days_per_month_final_year=pace_final_year,
        days_per_month_filing_recent=pace_filing_recent,
        days_per_month_filing_year=pace_filing_year,
    )


def _avg_pace_days_per_month(history: list[BulletinRow], field) -> float:
    cleaned = [field(b) for b in history if field(b) and field(b) != CURRENT_SENTINEL]
    if len(cleaned) < 2:
        return 0.0
    deltas = [(b - a).days for a, b in zip(cleaned, cleaned[1:])]
    return sum(deltas) / len(deltas)


def _eta_range(latest_cutoff: date | None, priority_date: date,
                pace_recent: float, pace_year: float,
                already_current: bool) -> tuple[date, date] | None:
    if already_current or latest_cutoff is None:
        return None
    if latest_cutoff == CURRENT_SENTINEL:
        return None
    gap_days = (priority_date - latest_cutoff).days
    if gap_days <= 0:
        return None
    # months to cover at each pace -> calendar days to wait at 30 days/month
    paces = [p for p in (pace_recent, pace_year) if p > 0]
    if not paces:
        return None
    # months_needed = gap_days / pace_days_per_month
    # calendar_days_to_wait ≈ months_needed * 30
    waits = sorted([round(gap_days / p * 30) for p in paces])
    today = date.today()
    return (today + timedelta(days=waits[0]), today + timedelta(days=waits[-1]))
