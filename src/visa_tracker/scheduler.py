from __future__ import annotations
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from visa_tracker.scraper import BulletinScraper

log = logging.getLogger(__name__)


def start_scheduler(scraper: BulletinScraper, *, timezone: str) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=timezone)
    # Hourly during the 8th-20th of each month
    scheduler.add_job(
        scraper.check_for_new_bulletins,
        CronTrigger.from_crontab("0 * 8-20 * *", timezone=timezone),
        id="hourly_window",
        max_instances=1,
        coalesce=True,
    )
    # Daily fallback at noon
    scheduler.add_job(
        scraper.check_for_new_bulletins,
        CronTrigger.from_crontab("0 12 * * *", timezone=timezone),
        id="daily_noon",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info("scheduler started: hourly during 8-20th, daily at noon (%s)", timezone)
    return scheduler
