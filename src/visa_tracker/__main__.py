from __future__ import annotations
import asyncio
import logging
import os
import signal
from pathlib import Path

import uvicorn

from visa_tracker.config import Settings
from visa_tracker.db import Database
from visa_tracker.notifier import Notifier, telegram_sender_factory
from visa_tracker.scheduler import start_scheduler
from visa_tracker.scraper import BulletinScraper
from visa_tracker.web import create_app


def _seed_path() -> Path:
    return Path(__file__).resolve().parents[2] / "seed.json"


async def _build_notifier(db: Database, settings: Settings) -> Notifier:
    if settings.telegram_enabled:
        sender = await telegram_sender_factory(
            settings.telegram_bot_token, settings.telegram_chat_id)
    else:
        async def sender(text: str):
            logging.getLogger("notifier").info(
                "telegram disabled, would send:\n%s", text)
    return Notifier(db=db, sender=sender, priority_date=settings.priority_date)


async def _run(settings: Settings, log: logging.Logger) -> None:
    db = Database(settings.db_path)
    db.init_schema()
    inserted = db.load_seed_if_empty(_seed_path())
    if inserted:
        log.info("seeded %d bulletin rows from seed.json", inserted)

    notifier = await _build_notifier(db, settings)
    scraper = BulletinScraper(db=db, notifier=notifier)
    # AsyncIOScheduler.start() must be called from inside a running event loop
    scheduler = start_scheduler(scraper, timezone=os.environ.get("TZ", "UTC"))

    app = create_app(db=db, priority_date=settings.priority_date)
    host, port = settings.web_bind.split(":")
    config = uvicorn.Config(app=app, host=host, port=int(port),
                              log_level=settings.log_level.lower(), loop="none")
    server = uvicorn.Server(config)

    def _shutdown(*_):
        log.info("shutdown signal received")
        scheduler.shutdown(wait=False)
        server.should_exit = True

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log.info("starting web on %s", settings.web_bind)
    await server.serve()


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("visa_tracker")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run(settings, log))


if __name__ == "__main__":
    main()
