from __future__ import annotations
import argparse
import asyncio
from datetime import date, datetime
from pathlib import Path
import sys

from visa_tracker.config import Settings
from visa_tracker.db import Database, BulletinRow
from visa_tracker.notifier import (
    Notifier, telegram_sender_factory,
    render_new_bulletin_message, render_pd_crossed_message,
)
from visa_tracker.scraper import BulletinScraper, ParsedBulletin


def render_message_for_bulletin(db: Database, bulletin_month: str,
                                 priority_date: date) -> str:
    row = db.get_bulletin(bulletin_month)
    if row is None:
        raise KeyError(f"No bulletin for {bulletin_month}")
    prev = db.previous_bulletin(bulletin_month)
    return render_new_bulletin_message(row, prev, priority_date)


async def _make_notifier(db: Database, settings: Settings, *, dry_run: bool):
    if dry_run:
        async def _dry(text: str):
            print("---- would send Telegram ----")
            print(text)
            print("-----------------------------")
        sender = _dry
    elif settings.telegram_enabled:
        sender = await telegram_sender_factory(
            settings.telegram_bot_token, settings.telegram_chat_id)
    else:
        async def _noop(text: str):
            print("[telegram disabled] message:\n" + text)
        sender = _noop
    return Notifier(db=db, sender=sender, priority_date=settings.priority_date)


async def cmd_check_now(args, settings: Settings):
    db = Database(settings.db_path)
    db.init_schema()
    db.load_seed_if_empty(_seed_path())
    notifier = await _make_notifier(db, settings, dry_run=args.dry_run)
    scraper = BulletinScraper(db=db, notifier=notifier)
    # In dry-run mode we want no DB writes. Simplest: run on an in-memory copy.
    if args.dry_run:
        # Re-init using :memory: with seeded state cloned
        mem_db = Database(":memory:")
        mem_db.init_schema()
        for b in db.all_bulletins(include_simulated=True):
            mem_db.insert_bulletin(
                b.bulletin_month,
                ParsedBulletin(b.final_action_date, b.dates_for_filing,
                               b.raw_final_action, b.raw_dates_filing),
                b.source_url, simulated=b.simulated)
        for kind in ("new_bulletin", "pd_crossed_filing", "pd_crossed_final"):
            for m in mem_db.all_bulletin_months():
                mem_db.mark_notification_sent(kind, m)
        notifier = await _make_notifier(mem_db, settings, dry_run=True)
        scraper = BulletinScraper(db=mem_db, notifier=notifier)
    result = await scraper.check_for_new_bulletins()
    if result.error:
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    if result.new_bulletin_months:
        print(f"discovered: {', '.join(result.new_bulletin_months)}")
    else:
        print("no new bulletins")
    return 0


async def cmd_simulate(args, settings: Settings):
    db = Database(settings.db_path)
    db.init_schema()
    db.load_seed_if_empty(_seed_path())
    parsed = ParsedBulletin(
        final_action_date=date.fromisoformat(args.final_action),
        dates_for_filing=date.fromisoformat(args.filing),
        raw_final_action="(sim)",
        raw_dates_filing="(sim)",
    )
    if args.dry_run:
        prev = db.previous_bulletin(args.month)
        synth = BulletinRow(
            bulletin_month=args.month,
            final_action_date=parsed.final_action_date,
            dates_for_filing=parsed.dates_for_filing,
            source_url="(simulated)",
            raw_final_action="(sim)", raw_dates_filing="(sim)",
            fetched_at=datetime.now(),
            simulated=True,
        )
        msg = render_new_bulletin_message(synth, prev, settings.priority_date)
        print("---- would send Telegram ----")
        print(msg)
        print("-----------------------------")
        return 0
    notifier = await _make_notifier(db, settings, dry_run=False)
    db.insert_bulletin(args.month, parsed, source_url="(simulated)", simulated=True)
    new_row = db.get_bulletin(args.month)
    prev = db.previous_bulletin(args.month)
    await notifier.handle_new_bulletin(new_row, prev)
    print(f"simulated bulletin {args.month} inserted (simulated=1)")
    return 0


async def cmd_render_message(args, settings: Settings):
    db = Database(settings.db_path)
    db.init_schema()
    print(render_message_for_bulletin(db, args.bulletin_month, settings.priority_date))
    return 0


async def cmd_test_telegram(args, settings: Settings):
    if not settings.telegram_enabled:
        print("Telegram not configured (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)",
              file=sys.stderr)
        return 2
    sender = await telegram_sender_factory(
        settings.telegram_bot_token, settings.telegram_chat_id)
    await sender("✅ visa-tracker reachable")
    print("sent")
    return 0


def _seed_path() -> Path:
    container_path = Path("/app/seed.json")
    if container_path.exists():
        return container_path
    return Path(__file__).resolve().parents[2] / "seed.json"


def main(argv: list[str] | None = None) -> int:
    settings = Settings()
    parser = argparse.ArgumentParser(prog="visa-tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check-now", help="run one scrape cycle immediately")
    p_check.add_argument("--dry-run", action="store_true")
    p_check.set_defaults(func=cmd_check_now)

    p_sim = sub.add_parser("simulate-bulletin", help="inject a hypothetical bulletin")
    p_sim.add_argument("--month", required=True, help="YYYY-MM")
    p_sim.add_argument("--final-action", required=True, help="YYYY-MM-DD")
    p_sim.add_argument("--filing", required=True, help="YYYY-MM-DD")
    p_sim.add_argument("--dry-run", action="store_true")
    p_sim.set_defaults(func=cmd_simulate)

    p_rm = sub.add_parser("render-message", help="print would-be telegram message")
    p_rm.add_argument("bulletin_month")
    p_rm.set_defaults(func=cmd_render_message)

    p_tt = sub.add_parser("test-telegram", help="send connectivity check message")
    p_tt.set_defaults(func=cmd_test_telegram)

    args = parser.parse_args(argv)
    return asyncio.run(args.func(args, settings))


if __name__ == "__main__":
    sys.exit(main())
