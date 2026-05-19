# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal Python service that monitors the U.S. State Department visa bulletin (specifically the F2B category, "All Chargeability Areas" column — applies to Armenia), exposes a dashboard at `localhost:8080`, and sends Telegram alerts when new bulletins land or when the priority date `2018-07-03` becomes current. Single Docker Compose deployable.

Full spec at `docs/superpowers/specs/2026-05-19-visa-bulletin-tracker-design.md`. The implementation plan and history live alongside it. Read the spec before making non-trivial changes — most behavioral decisions are documented there.

## Architecture in one paragraph

One Python process runs **FastAPI + APScheduler in the same asyncio event loop**. The scheduler fires `BulletinScraper.check_for_new_bulletins()` hourly during the 8th–20th of each month, daily at noon otherwise. The scraper reads the State Dept listing page (no URL prediction — the listing is the source of truth), diffs available bulletin months against the local SQLite `bulletins` table, fetches+parses any new ones, writes them, and asks the `Notifier` to fire Telegram alerts. The web app reads the same DB to render `/`, `/api/data`, and `/healthz`. Everything is keyed by `bulletin_month` (`YYYY-MM`) for idempotency.

## Commands

Test suite runs under 2s with no live network calls (real bulletin HTML fixtures live under `tests/fixtures/`).

```bash
# Local dev (venv at .venv was created via python3.13 -m venv .venv && pip install -e ".[dev]")
./.venv/bin/pytest                              # full suite
./.venv/bin/pytest tests/test_scraper.py -v     # one file
./.venv/bin/pytest tests/test_db.py::test_seed_loads_twelve_rows -v   # one test

# Docker-deployed runtime
docker compose build                            # MUST run after code changes (see "Stale image trap" below)
docker compose up -d
docker compose logs -f visa-tracker
docker compose down

# CLI subcommands inside the container
docker compose exec visa-tracker python -m visa_tracker.cli check-now [--dry-run]
docker compose exec visa-tracker python -m visa_tracker.cli simulate-bulletin --month YYYY-MM --final-action YYYY-MM-DD --filing YYYY-MM-DD [--dry-run]
docker compose exec visa-tracker python -m visa_tracker.cli render-message YYYY-MM
docker compose exec visa-tracker python -m visa_tracker.cli test-telegram

# Local app run (useful for debugging without rebuild). Override DB_PATH so it doesn't try to write to /data.
DB_PATH=./data/tracker.db WEB_BIND=127.0.0.1:8080 ./.venv/bin/python -m visa_tracker
```

## Non-obvious things to know before editing

**Stale image trap.** The most expensive bug in this repo's history: making code changes and forgetting `docker compose build`. The scheduler will run last-build code against a freshly-mounted `./data` volume and can fire hundreds of Telegram messages if your fix isn't actually deployed. **Always rebuild after edits before `docker compose up`.**

**Idempotency is keyed by `(event_kind, bulletin_month)` in `notifications_sent`.** Wiping a row there causes the next scrape to re-alert. The seed loader pre-marks all three event kinds for every seeded month — that's how we avoid retroactive spam on first boot. If you add a new event kind, the seed loader's `_NOTIFICATION_KINDS_PRE_SEEDED` tuple in `db.py` needs updating too.

**`MIN_BULLETIN_MONTH = "2025-01"` in `scraper.py` is load-bearing.** The State Dept listing page links to hundreds of historical bulletins going back to ~2002. Without this floor, the scraper would treat every one as "new", parse them (the old format mostly parses fine), and fire one Telegram alert per. The seed covers Jul 2025 – Jun 2026; the floor must stay at-or-below the seed's oldest entry.

**`simulate-bulletin` without `--dry-run` is a footgun.** It inserts a `simulated=1` row AND marks `notifications_sent` for `(new_bulletin, <month>)`. The dashboard hides simulated rows from charts/projection, BUT the notification mark will silently swallow the real alert when that month's bulletin actually lands. If you must run it for-real, also delete the corresponding `notifications_sent` rows afterward.

**Date sentinels.** `parse_visa_date` returns `CURRENT_SENTINEL = date(9999, 12, 31)` for `"C"` (current — no waiting) and `None` for `"U"` (unavailable). Both `_fmt_date` (notifier) and the dashboard JS must guard for these. The DB stores them as ISO `9999-12-31` and `NULL` respectively. F2B/Armenia hasn't been `C` in years, but the handling exists for correctness.

**Circular import.** `db.py` imports `ParsedBulletin` from `scraper.py` at module level. `scraper.py` needs `Database` only for type hints — guarded by `if TYPE_CHECKING:` with a forward-ref string. Don't "fix" this by adding a runtime import of `Database` in `scraper.py` — it'll break module load. The right structural fix would be to move `ParsedBulletin` to its own `models.py`; that's a known follow-up.

**`_seed_path()` checks two locations.** In the container, `seed.json` is at `/app/seed.json` (copied by Dockerfile). In dev (editable install from project root), it's at `parents[2]/seed.json`. The helpers in `__main__.py` and `cli.py` try the container path first, then fall back. If you change the Dockerfile layout, update both helpers.

**Tests must bypass `.env`.** Once `.env` exists locally with real credentials, pydantic-settings reads it whenever `Settings()` is constructed. Tests instantiate Settings with `_env_file=None` to stay isolated. New config tests must do the same.

**SQLite is WAL-mode and single-connection.** The `Database` class holds one `sqlite3.Connection` for the lifetime of the process. The scheduler and the FastAPI request handlers share it. This is safe because asyncio is single-threaded, but be careful introducing real threading — sqlite3 connections aren't thread-safe by default.

**No schema migrations.** `init_schema()` uses `CREATE TABLE IF NOT EXISTS` and never alters. Any schema change requires deleting `./data/tracker.db` (the seed will reload). For a personal tracker this is fine; do not assume future-you can ADD a column without thinking about this.

## Style guidance specific to this repo

- Strict type annotations. Avoid `Any`. Prefer `date | None` over `Optional[date]`.
- TDD via `pytest`/`pytest-asyncio`. Real HTML fixtures > mocks for the parser; `respx` for HTTP-layer integration tests.
- New dataclasses default to `@dataclass(frozen=True)` unless mutation is required.
- New `Notifier` events get their own `event_kind` string + a `render_*_message` helper. Reuse `_send_once` for the dedup pattern.
- Templates: keep CSS+JS inline in `dashboard.html` (single HTML response is the design choice).

## Files to skim first when getting oriented

| When you need to... | Read |
|---|---|
| Understand the data flow | `src/visa_tracker/scraper.py` — `BulletinScraper.check_for_new_bulletins` is the orchestrator |
| Change notification format | `src/visa_tracker/notifier.py` — render functions + `_format_chart_row` helper |
| Change DB schema or seed | `src/visa_tracker/db.py` + `seed.json` |
| Change projection math | `src/visa_tracker/projection.py` |
| Change the dashboard | `src/visa_tracker/templates/dashboard.html` + `src/visa_tracker/web.py` |
| Add a CLI subcommand | `src/visa_tracker/cli.py` |

## External integrations

- **State Dept listing page**: `https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html` is the only scrape target by design. All bulletin URLs come from this page; never construct them yourself.
- **Telegram Bot API**: `httpx` POST to `api.telegram.org/bot<TOKEN>/sendMessage`. Bot token + chat ID via env vars (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). Dedicated bot is `@disayan_visa_bulletin_bot`; credentials live in `.env` (gitignored) and 1Password Private vault.
- **GitHub origin**: `git@github.com:Diveafall/visa-tracker.git` (public).
