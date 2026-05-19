# Visa Bulletin Tracker — Design Spec

**Date:** 2026-05-19
**Status:** Approved (pending user spec review)
**Owner:** disayan

## Purpose

A personal service that monitors the U.S. Department of State Visa Bulletin and notifies the user when new bulletins are published or when their priority date becomes current. Runs anywhere via `docker compose up -d`.

## Goals

- Detect new monthly visa bulletins within an hour of publication during the typical release window.
- Surface F2B category dates for "All Chargeability Areas Except Those Listed" (the column applicable to Armenia).
- Send Telegram notifications on (a) every new bulletin and (b) when the hardcoded priority date `2018-07-03` becomes current on either chart.
- Provide a web dashboard with current dates, trend chart, history table, and a live projection of when the priority date will be reached.
- Be trivially runnable on any machine: one `docker compose up -d` command, no external services beyond Telegram (optional).

## Non-goals

- Multi-user or multi-category tracking. F2B + Armenia column only.
- Editable priority date in the UI. The PD is hardcoded (`2018-07-03`) but env-overridable.
- Tracking employment-based categories (EB-1, EB-2, etc.).
- Authentication on the web UI. Bound to localhost by default.
- Mobile-first design.

---

## 1. Architecture

A single Python process inside one Docker container. Four logical modules co-located:

```
┌──────────────────────────────────────────────────────────────┐
│  visa-tracker  (one process)                                 │
│                                                              │
│   ┌─────────────┐    ┌──────────────┐    ┌───────────────┐  │
│   │  Scheduler  │───▶│   Scraper    │───▶│  Notifier     │  │
│   │ APScheduler │    │  travel.gov  │    │ Telegram bot  │  │
│   └─────────────┘    └──────┬───────┘    └───────────────┘  │
│         ▲                   │                   ▲           │
│         │                   ▼                   │           │
│         │            ┌─────────────┐            │           │
│         │            │   SQLite    │────────────┘           │
│         │            │  bulletins  │  (read for diff +      │
│         │            └──────┬──────┘   projection)          │
│         │                   │                               │
│         │                   ▼                               │
│         │            ┌─────────────┐                        │
│         └────────────│   FastAPI   │── serves dashboard +   │
│                      │   web app   │   /api/data            │
│                      └─────────────┘                        │
└──────────────────────────────────────────────────────────────┘
       ▲                              ▲
       │ port 8080                    │ volume mount
   you (browser)                  ./data/  (sqlite + state)
```

**Components:**

- **Scheduler** — APScheduler `AsyncIOScheduler` with two cron jobs, both invoking `check_bulletin()`:
  - `0 * 8-20 * *` — hourly during the typical release window (8th–20th of each month).
  - `0 12 * * *` — daily fallback at noon for the rest of the month.
- **Scraper** — uses the official listing page as the single source of truth. No URL prediction.
- **Store** — SQLite at `/data/tracker.db`. Seeded on first boot from bundled `seed.json` (12 months of historical data).
- **Notifier** — async Telegram client. Three event kinds, each idempotent via `notifications_sent` table.
- **Web app** — FastAPI serving `GET /` (Jinja2 dashboard), `GET /api/data` (JSON), `GET /healthz` (healthcheck).

---

## 2. Data model

SQLite database at `/data/tracker.db`. Three tables.

```sql
-- One row per visa bulletin we've ever seen.
CREATE TABLE bulletins (
    bulletin_month     TEXT PRIMARY KEY,        -- 'YYYY-MM', e.g. '2026-06'
    final_action_date  TEXT,                    -- ISO 'YYYY-MM-DD'; NULL = unavailable ('U')
    dates_for_filing   TEXT,                    -- ISO 'YYYY-MM-DD'; NULL = unavailable
    source_url         TEXT NOT NULL,
    raw_final_action   TEXT NOT NULL,           -- original '22SEP17' / 'C' / 'U' for audit
    raw_dates_filing   TEXT NOT NULL,
    fetched_at         TEXT NOT NULL,           -- ISO timestamp (UTC)
    simulated          INTEGER NOT NULL DEFAULT 0   -- 1 = injected via simulate-bulletin CLI
);

-- Audit log of every scraper run, success or failure.
CREATE TABLE scrape_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,              -- 'ok' | 'no_change' | 'error' | 'new_bulletin'
    detail          TEXT,                       -- error msg or summary
    bulletin_month  TEXT                        -- if a bulletin was discovered
);

-- Idempotency for notifications. One row per (event_kind, bulletin_month).
CREATE TABLE notifications_sent (
    event_kind     TEXT NOT NULL,               -- 'new_bulletin' | 'pd_crossed_filing'
                                                -- | 'pd_crossed_final' | 'parser_broken'
    bulletin_month TEXT NOT NULL,
    sent_at        TEXT NOT NULL,
    PRIMARY KEY (event_kind, bulletin_month)
);
```

**Key decisions:**

- `bulletin_month` (e.g. `'2026-06'`) is the natural primary key. `INSERT OR REPLACE` is idempotent.
- Both raw and parsed date strings are stored so parser failures can be diagnosed.
- `final_action_date` and `dates_for_filing` are nullable to handle the `'U'` (unavailable) edge case. A sentinel handling decision for `'C'` (current): store as a far-future date (`9999-12-31`) so chart math still works; the renderer checks for this sentinel and displays "Current".
- SQLite is in WAL mode (`PRAGMA journal_mode=WAL`) so dashboard reads never block scraper writes.

**Seed data:** `seed.json` is a 12-element JSON array bundled in the Docker image (priority dates Jul 2025 – Jun 2026). On first boot, if `bulletins` is empty: insert all 12 rows AND insert corresponding `notifications_sent` rows for every `(event_kind, bulletin_month)` pair, pre-emptively marking them notified to suppress retroactive Telegram spam.

---

## 3. Scraping & bulletin detection

### Detection — listing-page driven, no URL prediction

Single canonical URL: `https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html`.

This page reliably exposes:
- A **"Current Visa Bulletin"** link.
- An **"Upcoming Visa Bulletin"** link (where new bulletins first appear).
- **Fiscal Year sections** listing all published bulletins, newest-first.

```python
def list_available_bulletins() -> list[BulletinLink]:
    html = http.get(LISTING_URL, timeout=20).text
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.select("a[href*='visa-bulletin-for-']"):
        href = urljoin(LISTING_URL, a["href"])
        month, year = _parse_month_year_from_href(href)
        bulletin_month = f"{year}-{MONTH_NUM[month]:02d}"
        links.append(BulletinLink(bulletin_month=bulletin_month, url=href))
    return _dedupe_keep_first(links)
```

### Diff against the DB

```python
def check_for_new_bulletins():
    available = list_available_bulletins()
    known = set(db.all_bulletin_months())
    new = [b for b in available if b.bulletin_month not in known]
    for bulletin in sorted(new, key=lambda b: b.bulletin_month):
        parsed = fetch_and_parse(bulletin.url)
        db.insert_bulletin(parsed)
        notifier.handle_new_bulletin(parsed, prev=db.previous_bulletin(parsed))
```

**Late-edit handling:** if a bulletin we already have shows different cutoff dates on re-fetch (rare State Dept correction), update the row and fire an "amended bulletin" Telegram message.

### Parsing F2B / "All other" cells

Both target charts are tables preceded by a heading matching `Final Action Dates` or `Dates for Filing`. The F2B row's "All Chargeability Areas Except Those Listed" cell is extracted from each.

```python
def parse_bulletin(html: str) -> ParsedBulletin:
    soup = BeautifulSoup(html, "html.parser")
    final_action = _extract_cell(soup, chart_heading="Final Action Dates",
                                 row_label="F2B", col_label="All Chargeability")
    dates_filing = _extract_cell(soup, chart_heading="Dates for Filing",
                                 row_label="F2B", col_label="All Chargeability")
    return ParsedBulletin(
        final_action_date=_parse_visa_date(final_action),
        dates_for_filing=_parse_visa_date(dates_filing),
        raw_final_action=final_action,
        raw_dates_filing=dates_filing,
    )
```

`_parse_visa_date`:
- `'22SEP17'` → `date(2017, 9, 22)`
- `'C'` → `date(9999, 12, 31)` (sentinel for "current")
- `'U'` → `None` (unavailable)
- Anything else → raises a parse error

### Robustness

- 20-second HTTP timeout per request.
- No in-run retries; the scheduler is the retry mechanism.
- User-Agent: `visa-tracker/1.0 (+personal use)`.
- Every run logged to `scrape_runs`.

---

## 4. Notifications (Telegram)

Three event kinds, all deduped per `(event_kind, bulletin_month)`.

### Event 1 — `new_bulletin`

Fires when a new `bulletin_month` row is inserted.

```
📅 New Visa Bulletin: July 2026

F2B — All Other (Armenia)
  ✅ Final Action:  22-OCT-2017  (+30 days vs. June)
  📋 Dates Filing:  15-APR-2018  (+24 days vs. June)

Your priority date: 03-JUL-2018
  → Final Action gap: ~8.4 months behind
  → Filing gap:       ~2.6 months behind

🔗 https://travel.state.gov/.../visa-bulletin-for-july-2026.html
```

Retrogression rendered with `⚠️` and negative delta.

### Event 2 — `pd_crossed_filing` / `pd_crossed_final`

Fires when, for a newly-inserted bulletin, the cutoff crossed the priority date `2018-07-03`:

```python
if prev.dates_for_filing < PD <= new.dates_for_filing:
    send("pd_crossed_filing", ...)
if prev.final_action_date < PD <= new.final_action_date:
    send("pd_crossed_final", ...)
```

```
🎉 PRIORITY DATE CURRENT — Final Action

Your PD (03-JUL-2018) is now ≤ Final Action cutoff (15-AUG-2018)
in the July 2026 bulletin.

This is the bulletin to watch for visa issuance.
🔗 https://travel.state.gov/...
```

### Event 3 — `parser_broken`

Fires once per `bulletin_month` when the scraper successfully fetches a bulletin page but the parser cannot extract the F2B row. Lets the user investigate within an hour rather than silently missing bulletins.

### Send path

```python
async def send(text: str, *, event_kind: str, bulletin_month: str):
    if db.notification_already_sent(event_kind, bulletin_month):
        return
    resp = await http.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown",
              "disable_web_page_preview": False},
        timeout=15,
    )
    resp.raise_for_status()
    db.mark_notification_sent(event_kind, bulletin_month)
```

- Telegram credentials from env (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). If absent, notifier no-ops with a warning; the web tracker still functions.
- A send failure does NOT mark the notification sent — next tick retries.
- 4xx responses (bad token, blocked) are terminal failures: logged and marked to stop the retry loop.

### Seed behavior

On first-time seed load, every `(event_kind, bulletin_month)` pair for the 12 seeded months is pre-inserted into `notifications_sent`. No retroactive spam.

---

## 5. Web UI

Single dashboard page, server-rendered with Jinja2, lightly enhanced with client-side JS for the projection. Same visual language as the existing `f2b-visa-trend.html` (dark theme, KPIs + chart + table).

### Routes

| Route | Returns | Purpose |
|---|---|---|
| `GET /` | HTML | Dashboard |
| `GET /api/data` | JSON | `{ bulletins, priority_date, last_check, projection }` |
| `GET /healthz` | 200 OK | Container healthcheck |

No authentication. Bound to `127.0.0.1:8080` by default.

### Page sections (top to bottom)

1. **Header** — title, priority date, "last checked: N minutes ago" badge.
2. **KPI strip** — latest Final Action, latest Filing, current gap to each from PD.
3. **Trend chart** — Chart.js line chart of both cutoffs over time, plus a dashed horizontal reference line at the priority date (`2018-07-03`).
4. **Projection panel** — server-side computed from last-3-month and last-12-month pace; presents both as a range. Re-renders on each bulletin.
5. **History table** — month-by-month, newest-first, with day-delta columns.
6. **Footer** — source link, last successful check, next scheduled cron tick.

### Out of scope (deliberately)

- No interactive PD picker (`/api/data` exposes the field for future expansion).
- No multi-category support.
- No login / accounts.
- No mobile-specific layout.

### Static assets

- Chart.js loaded from CDN.
- All CSS/JS inlined into the Jinja template — single HTML response.

---

## 6. Configuration & runtime

### Repository layout

```
visa-tracker/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml              # deps: httpx, beautifulsoup4, apscheduler,
│                               #       fastapi, uvicorn, jinja2, pydantic-settings
├── README.md
├── seed.json                   # 12 months of historical data, baked in
└── src/visa_tracker/
    ├── __main__.py             # entry point — starts FastAPI + scheduler
    ├── cli.py                  # dry-run / preview subcommands
    ├── config.py               # env var loading (Pydantic Settings)
    ├── db.py                   # sqlite + schema + seed loader
    ├── scraper.py              # listing-page + bulletin parsing
    ├── notifier.py             # Telegram client + dedup
    ├── scheduler.py            # APScheduler cron jobs
    ├── projection.py           # pace math (3-mo + 12-mo averages)
    ├── web.py                  # FastAPI app, /, /api/data, /healthz
    └── templates/
        └── dashboard.html      # Jinja2 — dark theme, Chart.js inline
```

### docker-compose.yml

```yaml
services:
  visa-tracker:
    build: .
    container_name: visa-tracker
    restart: unless-stopped
    ports:
      - "127.0.0.1:8080:8080"
    volumes:
      - ./data:/data
    environment:
      TELEGRAM_BOT_TOKEN: "${TELEGRAM_BOT_TOKEN:-}"
      TELEGRAM_CHAT_ID:   "${TELEGRAM_CHAT_ID:-}"
      TZ:                 "America/Los_Angeles"
      LOG_LEVEL:          "INFO"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')"]
      interval: 60s
      timeout: 5s
      retries: 3
```

### .env.example

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

### Dockerfile

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY src ./src
COPY seed.json ./
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "visa_tracker"]
```

### Environment variables

| Var | Required | Default | Purpose |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | No | empty | If empty, Telegram disabled |
| `TELEGRAM_CHAT_ID` | No | empty | Required if token is set |
| `TZ` | No | `UTC` | Cron interpretation timezone |
| `LOG_LEVEL` | No | `INFO` | `DEBUG` for diagnosis |
| `WEB_BIND` | No | `0.0.0.0:8080` | Bind address inside container |
| `PRIORITY_DATE` | No | `2018-07-03` | Tracked priority date |

### Onboarding

```bash
git clone <repo>
cd visa-tracker
cp .env.example .env       # fill in bot token + chat ID (optional)
docker compose up -d
open http://localhost:8080
```

---

## 7. Error handling & resilience

| Failure | Handling |
|---|---|
| Network error on scrape | Log `error` in `scrape_runs`, no DB write, no notification. Next tick retries. |
| Listing page reachable, no bulletin links | Log error with HTML snippet. After 3 consecutive failures, fire `parser_broken` Telegram alert (deduped per-day). |
| Bulletin page parses but F2B row missing | Log error. Fire `parser_broken` Telegram alert (deduped per `bulletin_month`). |
| Cutoff cell has unexpected value | `_parse_visa_date` returns sentinels; nullable columns handle them. |
| Telegram API down or 5xx | `notifications_sent` row NOT written. Next tick retries. |
| Telegram returns 4xx (bad token, blocked) | Logged, retry loop terminated for that notification. |
| SQLite locked | WAL mode means web reads don't block writes. |
| Clock drift / DST | `TZ` env var pins APScheduler interpretation. Cron windows loose enough to absorb drift. |
| Container restart mid-scrape | Scrapes are atomic per-bulletin-month. Next tick rediscovers. |
| Disk full | SQLite write fails, logged. Healthcheck still passes briefly. Surfaces in dashboard "last check" badge. |

**Principles:**
- No silent failures — operational alerts via dashboard badge and `parser_broken` Telegram event.
- Idempotency end-to-end. All writes keyed by `bulletin_month` or `(event_kind, bulletin_month)`.
- One try per HTTP request per scheduler tick. The scheduler IS the retry mechanism.

---

## 8. Testing strategy

`pytest` + `pytest-asyncio`. Target: full suite <10 seconds, no live network.

### Unit tests

| Module | What is tested | Approach |
|---|---|---|
| `scraper.parse_bulletin` | F2B row extraction | Snapshot fixtures: real HTML for Jun 2026, Apr 2026, Nov 2025 under `tests/fixtures/` |
| `scraper.list_available_bulletins` | Listing page → list of months | Fixture: `tests/fixtures/listing-page.html` |
| `scraper._parse_visa_date` | `'22SEP17'`, `'C'`, `'U'`, malformed | Parametrized |
| `notifier.handle_new_bulletin` | Message body + dedup + retry-on-failure | In-memory SQLite, fake Telegram client |
| `notifier.handle_priority_date_crossing` | PD crossing on both charts | Parametrized: behind / crossing / already past |
| `projection.compute` | 3-month & 12-month pace math | Synthetic history, ±15 day tolerance |
| `db` schema & seed | Seed loads 12 rows, dedup works | In-memory SQLite |

### Integration tests

| Test | Approach |
|---|---|
| Full "new bulletin discovered" flow | `respx` mocks listing page + bulletin HTML. Assert insert + notify + no-op on second run. |
| Bulletin amendment flow | Existing row, dates differ on refetch. Assert update + amended notification. |
| Web routes | `httpx.AsyncClient(app=fastapi_app)`. Assert `/`, `/api/data`, `/healthz` return 200 with expected shape. |

### Not tested

- APScheduler cron parsing (library).
- Real network to travel.state.gov.
- Real Telegram sends (verified manually via `test-telegram` CLI).
- Docker compose (verified manually once).

---

## 9. Dry-run & preview modes

CLI subcommands, runnable via `docker compose exec visa-tracker python -m visa_tracker.cli <cmd>`.

### `check-now`

Forces one scrape cycle immediately. Writes and sends Telegram as normal. Use after first deploy.

### `check-now --dry-run`

Scrapes for real, but does NOT write to DB or send Telegram. Prints what *would* happen, including the full Telegram message body that would be sent for any new bulletin. Safe to run any time. Verifies parser still works.

### `simulate-bulletin --month YYYY-MM --final-action YYYY-MM-DD --filing YYYY-MM-DD [--dry-run]`

Synthesize a hypothetical bulletin and run it through diff + notification pipeline. With `--dry-run`: pure preview. Without: inserts a row with `simulated=1`, which the dashboard excludes from charts and projections.

### `render-message <bulletin_month>`

Prints the Telegram message body that would be sent for an existing bulletin row. Zero side effects. Useful for tweaking message templates.

### `test-telegram`

Sends a connectivity check message using current env vars. Surfaces auth errors immediately.

---

## Open questions

None at spec-approval time. (Any that emerge during planning go into the implementation plan, not back into this spec.)

## Risks

- **State Dept changes HTML layout.** Mitigated by `parser_broken` Telegram alert (you find out within an hour, not months).
- **State Dept rate-limits or blocks the user-agent.** Low risk at 1 req/hr. Mitigated by polite UA string and 20s timeout.
- **Telegram bot token leaked.** Mitigated by `.env` gitignored. Worst case: regenerate via @BotFather.
- **SQLite corruption.** Mitigated by WAL mode + simple schema. Worst case: delete `./data/tracker.db`, restart, re-seeds from JSON; loses any new bulletins gathered since seed (re-discoverable on next scrape).
