from __future__ import annotations
import logging
from datetime import date
from typing import Awaitable, Callable

import httpx

from visa_tracker.db import Database, BulletinRow
from visa_tracker.parsing import CURRENT_SENTINEL

log = logging.getLogger(__name__)

Sender = Callable[[str], Awaitable[None]]

_MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]


def _fmt_date(d: date | None) -> str:
    if d is None:
        return "U (unavailable)"
    if d == CURRENT_SENTINEL:
        return "C (Current)"
    return d.strftime("%d-%b-%Y").upper()


def _month_name(bulletin_month: str) -> str:
    yr, mo = bulletin_month.split("-")
    return f"{_MONTH_NAMES[int(mo)]} {yr}"


def _short_month(bulletin_month: str) -> str:
    _, mo = bulletin_month.split("-")
    return _MONTH_NAMES[int(mo)]


def _format_chart_row(label: str, new_d: date | None, prev_d: date | None,
                       prev_month: str | None, delta: int | None) -> str:
    inner_parts: list[str] = []
    if prev_month is not None and prev_d is not None:
        inner_parts.append(f"{prev_month}: {_fmt_date(prev_d)}")
    if delta is not None:
        inner_parts.append(_fmt_delta(delta))
    suffix = f"  ({', '.join(inner_parts)})" if inner_parts else ""
    return f"  {label}: {_fmt_date(new_d)}{suffix}"


def _delta_days(a: date | None, b: date | None) -> int | None:
    if a is None or b is None:
        return None
    return (b - a).days


def _fmt_delta(delta: int | None) -> str:
    if delta is None:
        return "n/a"
    if delta > 0:
        return f"+{delta} days"
    if delta < 0:
        return f"⚠️ {delta} days"
    return "0 days"


def render_new_bulletin_message(new: BulletinRow, prev: BulletinRow | None,
                                  priority_date: date) -> str:
    final_delta = _delta_days(prev.final_action_date if prev else None,
                               new.final_action_date)
    filing_delta = _delta_days(prev.dates_for_filing if prev else None,
                                new.dates_for_filing)
    gap_final = _delta_days(new.final_action_date, priority_date)
    gap_filing = _delta_days(new.dates_for_filing, priority_date)
    prev_month = _short_month(prev.bulletin_month) if prev else None

    lines = [
        f"📅 New Visa Bulletin: {_month_name(new.bulletin_month)}",
        "",
        "F2B — All Other (Armenia)",
        _format_chart_row("✅ Final Action", new.final_action_date,
                          prev.final_action_date if prev else None,
                          prev_month, final_delta),
        _format_chart_row("📋 Dates Filing", new.dates_for_filing,
                          prev.dates_for_filing if prev else None,
                          prev_month, filing_delta),
        "",
        f"Your priority date: {_fmt_date(priority_date)}",
    ]
    if gap_final is not None:
        if gap_final > 0:
            lines.append(f"  → Final Action gap: ~{gap_final / 30:.1f} months behind")
        else:
            lines.append("  → Final Action: ✅ priority date is current")
    if gap_filing is not None:
        if gap_filing > 0:
            lines.append(f"  → Filing gap:       ~{gap_filing / 30:.1f} months behind")
        else:
            lines.append("  → Filing: ✅ priority date is current")
    lines += ["", f"🔗 {new.source_url}"]
    return "\n".join(lines)


def render_pd_crossed_message(new: BulletinRow, chart: str, priority_date: date) -> str:
    label = "Final Action" if chart == "final_action" else "Filing"
    cutoff = new.final_action_date if chart == "final_action" else new.dates_for_filing
    return "\n".join([
        f"🎉 PRIORITY DATE CURRENT — {label}",
        "",
        f"Your PD ({_fmt_date(priority_date)}) is now ≤ {label} cutoff "
        f"({_fmt_date(cutoff)})",
        f"in the {_month_name(new.bulletin_month)} bulletin.",
        "",
        f"🔗 {new.source_url}",
    ])


class Notifier:
    def __init__(self, *, db: Database, sender: Sender, priority_date: date):
        self.db = db
        self.sender = sender
        self.priority_date = priority_date

    async def _send_once(self, event_kind: str, bulletin_month: str, text: str) -> None:
        if self.db.notification_already_sent(event_kind, bulletin_month):
            return
        try:
            await self.sender(text)
        except httpx.HTTPStatusError as e:
            if 400 <= e.response.status_code < 500:
                log.warning(
                    "telegram 4xx for (%s, %s): %d %s — marking sent to stop retry loop",
                    event_kind, bulletin_month, e.response.status_code, e.response.text[:200],
                )
                self.db.mark_notification_sent(event_kind, bulletin_month)
                return
            raise
        self.db.mark_notification_sent(event_kind, bulletin_month)

    async def handle_parser_broken(self, bulletin_month: str, detail: str) -> None:
        msg = (
            "⚠️ visa-tracker: parser failed\n\n"
            f"Bulletin: {bulletin_month}\n"
            f"Detail: {detail}\n\n"
            "Inspect the bulletin page and update the parser."
        )
        await self._send_once("parser_broken", bulletin_month, msg)

    async def handle_amended_bulletin(self, new: BulletinRow, prev: BulletinRow | None,
                                       stale: BulletinRow) -> None:
        msg = (
            f"📝 Bulletin Amended: {_month_name(new.bulletin_month)}\n\n"
            f"F2B — All Other (Armenia)\n"
            f"  Final Action: {_fmt_date(stale.final_action_date)} → "
            f"{_fmt_date(new.final_action_date)}\n"
            f"  Dates Filing: {_fmt_date(stale.dates_for_filing)} → "
            f"{_fmt_date(new.dates_for_filing)}\n\n"
            f"🔗 {new.source_url}"
        )
        await self._send_once("amended_bulletin", new.bulletin_month, msg)

    async def handle_new_bulletin(self, new: BulletinRow, prev: BulletinRow | None) -> None:
        msg = render_new_bulletin_message(new, prev, self.priority_date)
        await self._send_once("new_bulletin", new.bulletin_month, msg)
        # PD crossing checks
        if prev is not None:
            if (prev.dates_for_filing is not None and new.dates_for_filing is not None
                and prev.dates_for_filing < self.priority_date <= new.dates_for_filing):
                await self._send_once(
                    "pd_crossed_filing", new.bulletin_month,
                    render_pd_crossed_message(new, "filing", self.priority_date))
            if (prev.final_action_date is not None and new.final_action_date is not None
                and prev.final_action_date < self.priority_date <= new.final_action_date):
                await self._send_once(
                    "pd_crossed_final", new.bulletin_month,
                    render_pd_crossed_message(new, "final_action", self.priority_date))


async def telegram_sender_factory(bot_token: str, chat_id: str) -> Sender:
    """Return a Sender bound to a Telegram bot/chat."""
    async def _send(text: str) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text,
                      "disable_web_page_preview": False},
            )
            resp.raise_for_status()
    return _send
