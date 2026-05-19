from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import json
import sqlite3

from visa_tracker.scraper import ParsedBulletin


@dataclass(frozen=True)
class BulletinRow:
    bulletin_month: str
    final_action_date: date | None
    dates_for_filing: date | None
    source_url: str
    raw_final_action: str
    raw_dates_filing: str
    fetched_at: datetime
    simulated: bool


@dataclass(frozen=True)
class ScrapeRunRow:
    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    detail: str | None
    bulletin_month: str | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS bulletins (
    bulletin_month     TEXT PRIMARY KEY,
    final_action_date  TEXT,
    dates_for_filing   TEXT,
    source_url         TEXT NOT NULL,
    raw_final_action   TEXT NOT NULL,
    raw_dates_filing   TEXT NOT NULL,
    fetched_at         TEXT NOT NULL,
    simulated          INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS scrape_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,
    detail          TEXT,
    bulletin_month  TEXT
);
CREATE TABLE IF NOT EXISTS notifications_sent (
    event_kind     TEXT NOT NULL,
    bulletin_month TEXT NOT NULL,
    sent_at        TEXT NOT NULL,
    PRIMARY KEY (event_kind, bulletin_month)
);
"""

_NOTIFICATION_KINDS_PRE_SEEDED = (
    "new_bulletin", "pd_crossed_filing", "pd_crossed_final"
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso_date(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _parse_iso_date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)

    def list_tables(self) -> list[str]:
        cur = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return [row[0] for row in cur.fetchall()]

    def all_bulletin_months(self) -> list[str]:
        cur = self._conn.execute(
            "SELECT bulletin_month FROM bulletins WHERE simulated=0 ORDER BY bulletin_month"
        )
        return [row[0] for row in cur.fetchall()]

    def insert_bulletin(self, bulletin_month: str, parsed: ParsedBulletin,
                        source_url: str, *, simulated: bool) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO bulletins
            (bulletin_month, final_action_date, dates_for_filing, source_url,
             raw_final_action, raw_dates_filing, fetched_at, simulated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (bulletin_month,
             _iso_date(parsed.final_action_date),
             _iso_date(parsed.dates_for_filing),
             source_url,
             parsed.raw_final_action,
             parsed.raw_dates_filing,
             _iso_now(),
             1 if simulated else 0),
        )

    def get_bulletin(self, bulletin_month: str) -> BulletinRow | None:
        row = self._conn.execute(
            "SELECT * FROM bulletins WHERE bulletin_month=?", (bulletin_month,)
        ).fetchone()
        return self._row_to_bulletin(row) if row else None

    def previous_bulletin(self, bulletin_month: str) -> BulletinRow | None:
        row = self._conn.execute(
            """SELECT * FROM bulletins
            WHERE bulletin_month < ? AND simulated=0
            ORDER BY bulletin_month DESC LIMIT 1""",
            (bulletin_month,)
        ).fetchone()
        return self._row_to_bulletin(row) if row else None

    def all_bulletins(self, *, include_simulated: bool = False) -> list[BulletinRow]:
        if include_simulated:
            cur = self._conn.execute("SELECT * FROM bulletins ORDER BY bulletin_month")
        else:
            cur = self._conn.execute(
                "SELECT * FROM bulletins WHERE simulated=0 ORDER BY bulletin_month"
            )
        return [self._row_to_bulletin(r) for r in cur.fetchall()]

    def _row_to_bulletin(self, row: sqlite3.Row) -> BulletinRow:
        return BulletinRow(
            bulletin_month=row["bulletin_month"],
            final_action_date=_parse_iso_date(row["final_action_date"]),
            dates_for_filing=_parse_iso_date(row["dates_for_filing"]),
            source_url=row["source_url"],
            raw_final_action=row["raw_final_action"],
            raw_dates_filing=row["raw_dates_filing"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            simulated=bool(row["simulated"]),
        )

    def log_scrape_run(self, *, status: str, detail: str | None = None,
                       bulletin_month: str | None = None) -> None:
        now = _iso_now()
        self._conn.execute(
            """INSERT INTO scrape_runs (started_at, finished_at, status, detail, bulletin_month)
            VALUES (?, ?, ?, ?, ?)""",
            (now, now, status, detail, bulletin_month),
        )

    def recent_scrape_runs(self, *, limit: int) -> list[ScrapeRunRow]:
        cur = self._conn.execute(
            "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [
            ScrapeRunRow(
                id=row["id"],
                started_at=datetime.fromisoformat(row["started_at"]),
                finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
                status=row["status"],
                detail=row["detail"],
                bulletin_month=row["bulletin_month"],
            )
            for row in cur.fetchall()
        ]

    def notification_already_sent(self, event_kind: str, bulletin_month: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM notifications_sent WHERE event_kind=? AND bulletin_month=?",
            (event_kind, bulletin_month),
        ).fetchone()
        return row is not None

    def mark_notification_sent(self, event_kind: str, bulletin_month: str) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO notifications_sent (event_kind, bulletin_month, sent_at)
            VALUES (?, ?, ?)""",
            (event_kind, bulletin_month, _iso_now()),
        )

    def load_seed_if_empty(self, seed_path: Path) -> int:
        if self.all_bulletin_months():
            return 0
        with open(seed_path) as f:
            seeds = json.load(f)
        for entry in seeds:
            parsed = ParsedBulletin(
                final_action_date=_parse_iso_date(entry["final_action_date"]),
                dates_for_filing=_parse_iso_date(entry["dates_for_filing"]),
                raw_final_action=entry["raw_final_action"],
                raw_dates_filing=entry["raw_dates_filing"],
            )
            self.insert_bulletin(
                bulletin_month=entry["bulletin_month"],
                parsed=parsed,
                source_url=entry["source_url"],
                simulated=False,
            )
            for kind in _NOTIFICATION_KINDS_PRE_SEEDED:
                self.mark_notification_sent(kind, entry["bulletin_month"])
        return len(seeds)
