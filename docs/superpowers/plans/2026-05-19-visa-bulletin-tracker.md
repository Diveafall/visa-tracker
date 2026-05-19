# Visa Bulletin Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-container Python service that monitors the U.S. State Department visa bulletin, exposes a web dashboard with chart + projection, and notifies a Telegram bot on new bulletins and priority-date crossings.

**Architecture:** One Python process running FastAPI (dashboard + JSON API) and APScheduler (cron-driven scraper) in the same event loop. SQLite persistence at `/data/tracker.db`. Bundled `seed.json` populates the DB on first boot with 12 months of historical bulletins. Listing-page-driven detection (no URL prediction). Idempotent notifications keyed by `(event_kind, bulletin_month)`.

**Tech Stack:** Python 3.13, `httpx`, `beautifulsoup4`, `apscheduler`, `fastapi`, `uvicorn`, `jinja2`, `pydantic-settings`, `pytest`, `pytest-asyncio`, `respx`. Packaged as `python:3.13-slim` Docker image, orchestrated via `docker compose`.

**Working directory:** `/Users/disayan/code/personal/visa-tracker/` (git repo with the spec already committed).

**Reference spec:** `docs/superpowers/specs/2026-05-19-visa-bulletin-tracker-design.md`

---

## Task 1: Project skeleton & dependencies

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/visa_tracker/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "visa-tracker"
version = "0.1.0"
description = "Monitors U.S. State Department visa bulletin and notifies via Telegram"
requires-python = ">=3.13"
dependencies = [
    "httpx>=0.27",
    "beautifulsoup4>=4.12",
    "apscheduler>=3.10",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "jinja2>=3.1",
    "pydantic-settings>=2.6",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "respx>=0.21",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.py[cod]
.pytest_cache/
.venv/
venv/
data/
.env
*.db
*.db-wal
*.db-shm
.DS_Store
```

- [ ] **Step 3: Create empty package files**

Create empty files: `src/visa_tracker/__init__.py`, `tests/__init__.py`.

Write `tests/conftest.py`:
```python
import asyncio
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 4: Create venv and install**

```bash
cd /Users/disayan/code/personal/visa-tracker
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Expected: clean install, `pytest --version` returns 8.x.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore src/ tests/
git commit -m "chore: project skeleton with deps and pytest config"
```

---

## Task 2: Configuration loader

**Files:**
- Create: `src/visa_tracker/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
from datetime import date
from visa_tracker.config import Settings


def test_defaults_when_no_env(monkeypatch):
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                "LOG_LEVEL", "WEB_BIND", "PRIORITY_DATE", "DB_PATH"):
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    assert s.telegram_bot_token == ""
    assert s.telegram_chat_id == ""
    assert s.log_level == "INFO"
    assert s.web_bind == "0.0.0.0:8080"
    assert s.priority_date == date(2018, 7, 3)
    assert s.db_path == "/data/tracker.db"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc:123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654")
    monkeypatch.setenv("PRIORITY_DATE", "2019-01-15")
    s = Settings()
    assert s.telegram_bot_token == "abc:123"
    assert s.telegram_chat_id == "987654"
    assert s.priority_date == date(2019, 1, 15)


def test_telegram_enabled(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert Settings().telegram_enabled is False
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    assert Settings().telegram_enabled is True
```

- [ ] **Step 2: Verify the test fails**

Run: `pytest tests/test_config.py -v`
Expected: ImportError / ModuleNotFoundError on `visa_tracker.config`.

- [ ] **Step 3: Implement `src/visa_tracker/config.py`**

```python
from datetime import date
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                       extra="ignore")

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    log_level: str = "INFO"
    web_bind: str = "0.0.0.0:8080"
    priority_date: date = date(2018, 7, 3)
    db_path: str = "/data/tracker.db"

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token) and bool(self.telegram_chat_id)
```

- [ ] **Step 4: Verify the test passes**

Run: `pytest tests/test_config.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/visa_tracker/config.py tests/test_config.py
git commit -m "feat(config): env-driven settings with pydantic"
```

---

## Task 3: Visa date parser (the small pure function)

**Files:**
- Create: `src/visa_tracker/parsing.py`
- Create: `tests/test_parsing.py`

- [ ] **Step 1: Write the failing test**

`tests/test_parsing.py`:
```python
from datetime import date
import pytest
from visa_tracker.parsing import parse_visa_date, CURRENT_SENTINEL


@pytest.mark.parametrize("raw, expected", [
    ("22SEP17", date(2017, 9, 22)),
    ("01JAN18", date(2018, 1, 1)),
    ("15OCT16", date(2016, 10, 15)),
    ("08MAR17", date(2017, 3, 8)),
    ("22MAY17", date(2017, 5, 22)),
    ("01DEC16", date(2016, 12, 1)),
])
def test_parse_normal_dates(raw, expected):
    assert parse_visa_date(raw) == expected


def test_parse_current():
    assert parse_visa_date("C") == CURRENT_SENTINEL
    assert CURRENT_SENTINEL == date(9999, 12, 31)


def test_parse_unavailable():
    assert parse_visa_date("U") is None


def test_parse_whitespace():
    assert parse_visa_date("  22SEP17  ") == date(2017, 9, 22)


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        parse_visa_date("not a date")
    with pytest.raises(ValueError):
        parse_visa_date("99XYZ99")
```

- [ ] **Step 2: Verify the test fails**

Run: `pytest tests/test_parsing.py -v`
Expected: ImportError on `visa_tracker.parsing`.

- [ ] **Step 3: Implement `src/visa_tracker/parsing.py`**

```python
from datetime import date

CURRENT_SENTINEL = date(9999, 12, 31)

_MONTH_NUM = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def parse_visa_date(raw: str) -> date | None:
    """Parse the State Dept visa bulletin date format.

    Returns the parsed date for normal entries like '22SEP17',
    CURRENT_SENTINEL for 'C', and None for 'U' (unavailable).
    Raises ValueError on anything else.
    """
    s = raw.strip().upper()
    if s == "C":
        return CURRENT_SENTINEL
    if s == "U":
        return None
    if len(s) != 7:
        raise ValueError(f"Unexpected visa date format: {raw!r}")
    day_str, mon_str, yr_str = s[:2], s[2:5], s[5:7]
    if mon_str not in _MONTH_NUM or not day_str.isdigit() or not yr_str.isdigit():
        raise ValueError(f"Unparseable visa date: {raw!r}")
    yr = 2000 + int(yr_str)
    return date(yr, _MONTH_NUM[mon_str], int(day_str))
```

- [ ] **Step 4: Verify the tests pass**

Run: `pytest tests/test_parsing.py -v`
Expected: all parametrized cases + 4 standalone tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/visa_tracker/parsing.py tests/test_parsing.py
git commit -m "feat(parsing): visa bulletin date format parser"
```

---

## Task 4: HTML fixtures for scraper tests

**Files:**
- Create: `tests/fixtures/listing-page.html`
- Create: `tests/fixtures/bulletin-june-2026.html`
- Create: `tests/fixtures/bulletin-april-2026.html`
- Create: `tests/fixtures/bulletin-november-2025.html`

- [ ] **Step 1: Download fixtures with curl**

```bash
mkdir -p tests/fixtures
cd tests/fixtures

curl -sSL -A "visa-tracker/1.0 (+personal use)" \
  -o listing-page.html \
  "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"

curl -sSL -A "visa-tracker/1.0 (+personal use)" \
  -o bulletin-june-2026.html \
  "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2026/visa-bulletin-for-june-2026.html"

curl -sSL -A "visa-tracker/1.0 (+personal use)" \
  -o bulletin-april-2026.html \
  "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2026/visa-bulletin-for-april-2026.html"

curl -sSL -A "visa-tracker/1.0 (+personal use)" \
  -o bulletin-november-2025.html \
  "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2026/visa-bulletin-for-november-2025.html"
```

- [ ] **Step 2: Sanity-check each file**

```bash
for f in tests/fixtures/*.html; do
  echo "=== $f ==="
  wc -l "$f"
  grep -c "F2B" "$f"
done
```

Expected: each file > 100 lines and contains at least one "F2B" match.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/
git commit -m "test: add real HTML fixtures for scraper tests"
```

---

## Task 5: Bulletin page parser

**Files:**
- Create: `src/visa_tracker/scraper.py`
- Create: `tests/test_scraper.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_scraper.py`:
```python
from datetime import date
from pathlib import Path
import pytest
from visa_tracker.scraper import parse_bulletin

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("filename, expected_final, expected_filing", [
    ("bulletin-june-2026.html",     date(2017, 9, 22), date(2018, 3, 22)),
    ("bulletin-april-2026.html",    date(2017, 5, 22), date(2017, 8, 8)),
    ("bulletin-november-2025.html", date(2016, 12, 1), date(2017, 3, 8)),
])
def test_parse_bulletin_extracts_f2b_all_other(filename, expected_final, expected_filing):
    html = (FIXTURES / filename).read_text(encoding="utf-8")
    parsed = parse_bulletin(html)
    assert parsed.final_action_date == expected_final
    assert parsed.dates_for_filing == expected_filing


def test_parse_bulletin_preserves_raw_strings():
    html = (FIXTURES / "bulletin-june-2026.html").read_text(encoding="utf-8")
    parsed = parse_bulletin(html)
    assert parsed.raw_final_action == "22SEP17"
    assert parsed.raw_dates_filing == "22MAR18"


def test_parse_bulletin_missing_f2b_raises():
    with pytest.raises(ValueError, match="F2B"):
        parse_bulletin("<html><body>no tables here</body></html>")
```

- [ ] **Step 2: Verify the tests fail**

Run: `pytest tests/test_scraper.py -v`
Expected: ImportError on `visa_tracker.scraper`.

- [ ] **Step 3: Implement `src/visa_tracker/scraper.py`**

```python
from dataclasses import dataclass
from datetime import date
from bs4 import BeautifulSoup, Tag

from visa_tracker.parsing import parse_visa_date


@dataclass(frozen=True)
class ParsedBulletin:
    final_action_date: date | None
    dates_for_filing: date | None
    raw_final_action: str
    raw_dates_filing: str


def parse_bulletin(html: str) -> ParsedBulletin:
    soup = BeautifulSoup(html, "html.parser")
    raw_final = _extract_f2b_all_other(soup, "Final Action Dates")
    raw_filing = _extract_f2b_all_other(soup, "Dates for Filing")
    return ParsedBulletin(
        final_action_date=parse_visa_date(raw_final),
        dates_for_filing=parse_visa_date(raw_filing),
        raw_final_action=raw_final,
        raw_dates_filing=raw_filing,
    )


def _extract_f2b_all_other(soup: BeautifulSoup, chart_label: str) -> str:
    """Find the F2B row, 'All Other' column cell in the table matching chart_label."""
    table = _find_table_for_chart(soup, chart_label)
    if table is None:
        raise ValueError(f"Could not find table for chart {chart_label!r}")
    header_row = table.find("tr")
    if header_row is None:
        raise ValueError(f"No header row in chart {chart_label!r}")
    headers = [_cell_text(c) for c in header_row.find_all(["th", "td"])]
    col_index = next(
        (i for i, h in enumerate(headers) if "All Chargeability" in h or "All Other" in h),
        None,
    )
    if col_index is None:
        raise ValueError(f"Could not find 'All Chargeability' column in {chart_label!r}")

    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        label = _cell_text(cells[0])
        if label.upper().startswith("F2B"):
            if col_index >= len(cells):
                raise ValueError(f"F2B row missing column {col_index} in {chart_label!r}")
            return _cell_text(cells[col_index])
    raise ValueError(f"F2B row not found in chart {chart_label!r}")


def _find_table_for_chart(soup: BeautifulSoup, chart_label: str) -> Tag | None:
    """Return the first table whose preceding heading text contains chart_label."""
    for table in soup.find_all("table"):
        heading = _previous_heading_text(table)
        if heading and chart_label.lower() in heading.lower():
            return table
    return None


def _previous_heading_text(table: Tag) -> str:
    node = table
    while node is not None:
        node = node.find_previous(["h1", "h2", "h3", "h4", "p", "strong", "b"])
        if node is None:
            return ""
        text = node.get_text(" ", strip=True)
        if text:
            return text
    return ""


def _cell_text(cell: Tag) -> str:
    return cell.get_text(" ", strip=True)
```

- [ ] **Step 4: Verify the tests pass**

Run: `pytest tests/test_scraper.py -v`
Expected: all 5 tests pass. If a fixture parses to a different date than asserted, fix the assertion to match what's actually in the real bulletin file (the fixture is the ground truth).

- [ ] **Step 5: Commit**

```bash
git add src/visa_tracker/scraper.py tests/test_scraper.py
git commit -m "feat(scraper): parse F2B row and 'All Other' column from bulletin HTML"
```

---

## Task 6: Listing page parser

**Files:**
- Modify: `src/visa_tracker/scraper.py` (add `list_available_bulletins`)
- Modify: `tests/test_scraper.py` (add listing tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scraper.py`:
```python
from visa_tracker.scraper import list_available_bulletins, BulletinLink


def test_list_available_bulletins_returns_recent_months():
    html = (FIXTURES / "listing-page.html").read_text(encoding="utf-8")
    links = list_available_bulletins(html, base_url="https://travel.state.gov/")
    months = [b.bulletin_month for b in links]
    # June 2026 is the upcoming bulletin at fixture-capture time
    assert "2026-06" in months
    assert "2026-05" in months
    assert "2026-04" in months
    # newest-first ordering
    assert months == sorted(months, reverse=True)


def test_list_available_bulletins_dedupes():
    """'Current' link and FY section list the same URL — should appear once."""
    html = (FIXTURES / "listing-page.html").read_text(encoding="utf-8")
    links = list_available_bulletins(html, base_url="https://travel.state.gov/")
    months = [b.bulletin_month for b in links]
    assert len(months) == len(set(months))


def test_bulletin_link_url_is_absolute():
    html = (FIXTURES / "listing-page.html").read_text(encoding="utf-8")
    links = list_available_bulletins(html, base_url="https://travel.state.gov/")
    for b in links:
        assert b.url.startswith("https://")
```

- [ ] **Step 2: Verify the tests fail**

Run: `pytest tests/test_scraper.py -v`
Expected: ImportError on `list_available_bulletins` / `BulletinLink`.

- [ ] **Step 3: Extend `src/visa_tracker/scraper.py`**

Add at the top alongside the existing imports:
```python
import re
from urllib.parse import urljoin
```

Add the dataclass after `ParsedBulletin`:
```python
@dataclass(frozen=True)
class BulletinLink:
    bulletin_month: str   # 'YYYY-MM'
    url: str
```

Add the function:
```python
_HREF_RE = re.compile(r"visa-bulletin-for-([a-z]+)-(\d{4})\.html", re.IGNORECASE)
_MONTH_NUM = {m.lower(): i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june",
     "july", "august", "september", "october", "november", "december"])}


def list_available_bulletins(html: str, *, base_url: str) -> list[BulletinLink]:
    soup = BeautifulSoup(html, "html.parser")
    seen: dict[str, BulletinLink] = {}
    for a in soup.find_all("a", href=True):
        match = _HREF_RE.search(a["href"])
        if not match:
            continue
        month_word, year_str = match.group(1).lower(), match.group(2)
        month_num = _MONTH_NUM.get(month_word)
        if month_num is None:
            continue
        bulletin_month = f"{year_str}-{month_num:02d}"
        if bulletin_month in seen:
            continue
        seen[bulletin_month] = BulletinLink(
            bulletin_month=bulletin_month,
            url=urljoin(base_url, a["href"]),
        )
    return sorted(seen.values(), key=lambda b: b.bulletin_month, reverse=True)
```

- [ ] **Step 4: Verify the tests pass**

Run: `pytest tests/test_scraper.py -v`
Expected: 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/visa_tracker/scraper.py tests/test_scraper.py
git commit -m "feat(scraper): parse listing page for available bulletins"
```

---

## Task 7: Generate seed.json from fetched bulletins

**Files:**
- Create: `seed.json`

- [ ] **Step 1: Write `seed.json` by hand using the data we already have**

```json
[
  {"bulletin_month": "2025-07", "final_action_date": "2016-10-15", "dates_for_filing": "2017-01-01", "raw_final_action": "15OCT16", "raw_dates_filing": "01JAN17", "source_url": "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2025/visa-bulletin-for-july-2025.html"},
  {"bulletin_month": "2025-08", "final_action_date": "2016-10-15", "dates_for_filing": "2017-01-01", "raw_final_action": "15OCT16", "raw_dates_filing": "01JAN17", "source_url": "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2025/visa-bulletin-for-august-2025.html"},
  {"bulletin_month": "2025-09", "final_action_date": "2016-10-15", "dates_for_filing": "2017-01-01", "raw_final_action": "15OCT16", "raw_dates_filing": "01JAN17", "source_url": "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2025/visa-bulletin-for-september-2025.html"},
  {"bulletin_month": "2025-10", "final_action_date": "2016-11-22", "dates_for_filing": "2017-01-01", "raw_final_action": "22NOV16", "raw_dates_filing": "01JAN17", "source_url": "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2026/visa-bulletin-for-october-2025.html"},
  {"bulletin_month": "2025-11", "final_action_date": "2016-12-01", "dates_for_filing": "2017-03-08", "raw_final_action": "01DEC16", "raw_dates_filing": "08MAR17", "source_url": "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2026/visa-bulletin-for-november-2025.html"},
  {"bulletin_month": "2025-12", "final_action_date": "2016-12-01", "dates_for_filing": "2017-03-08", "raw_final_action": "01DEC16", "raw_dates_filing": "08MAR17", "source_url": "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2026/visa-bulletin-for-december-2025.html"},
  {"bulletin_month": "2026-01", "final_action_date": "2016-12-01", "dates_for_filing": "2017-03-15", "raw_final_action": "01DEC16", "raw_dates_filing": "15MAR17", "source_url": "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2026/visa-bulletin-for-january-2026.html"},
  {"bulletin_month": "2026-02", "final_action_date": "2016-12-01", "dates_for_filing": "2017-03-15", "raw_final_action": "01DEC16", "raw_dates_filing": "15MAR17", "source_url": "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2026/visa-bulletin-for-february-2026.html"},
  {"bulletin_month": "2026-03", "final_action_date": "2016-12-01", "dates_for_filing": "2017-03-15", "raw_final_action": "01DEC16", "raw_dates_filing": "15MAR17", "source_url": "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2026/visa-bulletin-for-march-2026.html"},
  {"bulletin_month": "2026-04", "final_action_date": "2017-05-22", "dates_for_filing": "2017-08-08", "raw_final_action": "22MAY17", "raw_dates_filing": "08AUG17", "source_url": "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2026/visa-bulletin-for-april-2026.html"},
  {"bulletin_month": "2026-05", "final_action_date": "2017-05-22", "dates_for_filing": "2018-01-01", "raw_final_action": "22MAY17", "raw_dates_filing": "01JAN18", "source_url": "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2026/visa-bulletin-for-may-2026.html"},
  {"bulletin_month": "2026-06", "final_action_date": "2017-09-22", "dates_for_filing": "2018-03-22", "raw_final_action": "22SEP17", "raw_dates_filing": "22MAR18", "source_url": "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/2026/visa-bulletin-for-june-2026.html"}
]
```

- [ ] **Step 2: Validate JSON**

Run: `python -c "import json; print(len(json.load(open('seed.json'))))"`
Expected output: `12`

- [ ] **Step 3: Commit**

```bash
git add seed.json
git commit -m "feat(seed): bundle 12 months of historical bulletins"
```

---

## Task 8: Database layer (schema + seed loader)

**Files:**
- Create: `src/visa_tracker/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_db.py`:
```python
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
```

- [ ] **Step 2: Verify the tests fail**

Run: `pytest tests/test_db.py -v`
Expected: ImportError on `visa_tracker.db`.

- [ ] **Step 3: Implement `src/visa_tracker/db.py`**

```python
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
```

- [ ] **Step 4: Verify the tests pass**

Run: `pytest tests/test_db.py -v`
Expected: 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/visa_tracker/db.py tests/test_db.py
git commit -m "feat(db): sqlite schema, seed loader, and bulletin/scrape-run/notification CRUD"
```

---

## Task 9: Projection math

**Files:**
- Create: `src/visa_tracker/projection.py`
- Create: `tests/test_projection.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_projection.py`:
```python
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
```

- [ ] **Step 2: Verify the tests fail**

Run: `pytest tests/test_projection.py -v`
Expected: ImportError on `visa_tracker.projection`.

- [ ] **Step 3: Implement `src/visa_tracker/projection.py`**

```python
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


def _avg_pace_days_per_month(history: list[BulletinRow],
                              field) -> float:
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
```

- [ ] **Step 4: Verify the tests pass**

Run: `pytest tests/test_projection.py -v`
Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/visa_tracker/projection.py tests/test_projection.py
git commit -m "feat(projection): compute ETA range from 3-mo and 12-mo pace averages"
```

---

## Task 10: Notifier (message rendering + send + dedup)

**Files:**
- Create: `src/visa_tracker/notifier.py`
- Create: `tests/test_notifier.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_notifier.py`:
```python
from datetime import date, datetime
import pytest
from visa_tracker.db import Database, BulletinRow
from visa_tracker.notifier import Notifier, render_new_bulletin_message, render_pd_crossed_message
from visa_tracker.scraper import ParsedBulletin


def _row(month: str, final: date, filing: date) -> BulletinRow:
    return BulletinRow(
        bulletin_month=month, final_action_date=final, dates_for_filing=filing,
        source_url=f"https://example.com/{month}", raw_final_action="x",
        raw_dates_filing="x", fetched_at=datetime(2026, 1, 1), simulated=False,
    )


def test_render_new_bulletin_includes_both_dates():
    new = _row("2026-07", date(2017, 10, 22), date(2018, 4, 15))
    prev = _row("2026-06", date(2017, 9, 22), date(2018, 3, 22))
    msg = render_new_bulletin_message(new, prev, priority_date=date(2018, 7, 3))
    assert "July 2026" in msg
    assert "22-OCT-2017" in msg
    assert "15-APR-2018" in msg
    assert "+30" in msg or "+30 days" in msg
    assert "03-JUL-2018" in msg


def test_render_pd_crossed_message():
    new = _row("2026-07", date(2018, 8, 1), date(2018, 9, 1))
    msg = render_pd_crossed_message(new, chart="final_action", priority_date=date(2018, 7, 3))
    assert "PRIORITY DATE CURRENT" in msg
    assert "Final Action" in msg
    assert "01-AUG-2018" in msg


class FakeSender:
    def __init__(self, fail_with: Exception | None = None):
        self.sent = []
        self.fail_with = fail_with

    async def __call__(self, text: str) -> None:
        if self.fail_with:
            raise self.fail_with
        self.sent.append(text)


@pytest.mark.asyncio
async def test_notifier_skips_if_already_sent(tmp_path):
    db = Database(str(tmp_path / "n.db"))
    db.init_schema()
    db.mark_notification_sent("new_bulletin", "2026-07")
    sender = FakeSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    new = _row("2026-07", date(2017, 10, 22), date(2018, 4, 15))
    await notifier.handle_new_bulletin(new, prev=None)
    assert sender.sent == []


@pytest.mark.asyncio
async def test_notifier_sends_and_marks(tmp_path):
    db = Database(str(tmp_path / "n.db"))
    db.init_schema()
    sender = FakeSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    new = _row("2026-07", date(2017, 10, 22), date(2018, 4, 15))
    prev = _row("2026-06", date(2017, 9, 22), date(2018, 3, 22))
    await notifier.handle_new_bulletin(new, prev=prev)
    assert len(sender.sent) == 1
    assert "July 2026" in sender.sent[0]
    assert db.notification_already_sent("new_bulletin", "2026-07")


@pytest.mark.asyncio
async def test_notifier_does_not_mark_on_send_failure(tmp_path):
    db = Database(str(tmp_path / "n.db"))
    db.init_schema()
    sender = FakeSender(fail_with=RuntimeError("network down"))
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    new = _row("2026-07", date(2017, 10, 22), date(2018, 4, 15))
    with pytest.raises(RuntimeError):
        await notifier.handle_new_bulletin(new, prev=None)
    assert not db.notification_already_sent("new_bulletin", "2026-07")


@pytest.mark.asyncio
async def test_notifier_fires_pd_crossed_when_pd_crosses_filing(tmp_path):
    db = Database(str(tmp_path / "n.db"))
    db.init_schema()
    sender = FakeSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    prev = _row("2026-06", date(2017, 9, 22), date(2018, 3, 22))
    new = _row("2026-07", date(2017, 10, 22), date(2018, 8, 1))  # filing crosses 2018-07-03
    await notifier.handle_new_bulletin(new, prev=prev)
    assert any("PRIORITY DATE CURRENT" in m and "Filing" in m for m in sender.sent)
```

- [ ] **Step 2: Verify the tests fail**

Run: `pytest tests/test_notifier.py -v`
Expected: ImportError on `visa_tracker.notifier`.

- [ ] **Step 3: Implement `src/visa_tracker/notifier.py`**

```python
from __future__ import annotations
from datetime import date
from typing import Awaitable, Callable

import httpx

from visa_tracker.db import Database, BulletinRow

Sender = Callable[[str], Awaitable[None]]

_MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]


def _fmt_date(d: date | None) -> str:
    if d is None:
        return "U (unavailable)"
    return d.strftime("%d-%b-%Y").upper()


def _month_name(bulletin_month: str) -> str:
    yr, mo = bulletin_month.split("-")
    return f"{_MONTH_NAMES[int(mo)]} {yr}"


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

    lines = [
        f"📅 New Visa Bulletin: {_month_name(new.bulletin_month)}",
        "",
        "F2B — All Other (Armenia)",
        f"  ✅ Final Action: {_fmt_date(new.final_action_date)}  ({_fmt_delta(final_delta)})",
        f"  📋 Dates Filing: {_fmt_date(new.dates_for_filing)}  ({_fmt_delta(filing_delta)})",
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
        await self.sender(text)
        self.db.mark_notification_sent(event_kind, bulletin_month)

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
```

- [ ] **Step 4: Verify the tests pass**

Run: `pytest tests/test_notifier.py -v`
Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/visa_tracker/notifier.py tests/test_notifier.py
git commit -m "feat(notifier): telegram message rendering, idempotent send, PD-crossing detection"
```

---

## Task 11: Scraper orchestration (HTTP fetch + diff + write)

**Files:**
- Modify: `src/visa_tracker/scraper.py` (add `BulletinScraper` class)
- Create: `tests/test_scraper_orchestration.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_scraper_orchestration.py`:
```python
from datetime import date
from pathlib import Path
import pytest
import respx
import httpx
from visa_tracker.db import Database
from visa_tracker.scraper import BulletinScraper
from visa_tracker.notifier import Notifier

FIXTURES = Path(__file__).parent / "fixtures"


class RecordingSender:
    def __init__(self):
        self.sent = []

    async def __call__(self, text: str):
        self.sent.append(text)


@pytest.mark.asyncio
@respx.mock
async def test_finds_new_bulletin_and_notifies(tmp_path):
    listing_html = (FIXTURES / "listing-page.html").read_text(encoding="utf-8")
    bulletin_html = (FIXTURES / "bulletin-june-2026.html").read_text(encoding="utf-8")

    respx.get(
        "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
    ).mock(return_value=httpx.Response(200, text=listing_html))
    respx.get(url__regex=r".*visa-bulletin-for-june-2026\.html$").mock(
        return_value=httpx.Response(200, text=bulletin_html))
    respx.get(url__regex=r".*visa-bulletin-for-.*\.html$").mock(
        return_value=httpx.Response(200, text=bulletin_html))

    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    sender = RecordingSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    scraper = BulletinScraper(db=db, notifier=notifier)

    result = await scraper.check_for_new_bulletins()
    assert result.new_bulletin_months  # at least one new
    assert "2026-06" in db.all_bulletin_months()


@pytest.mark.asyncio
@respx.mock
async def test_no_new_bulletins_is_noop(tmp_path):
    listing_html = (FIXTURES / "listing-page.html").read_text(encoding="utf-8")
    respx.get(
        "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
    ).mock(return_value=httpx.Response(200, text=listing_html))

    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    # Pre-populate DB with every month present in listing
    bulletin_html = (FIXTURES / "bulletin-june-2026.html").read_text(encoding="utf-8")
    respx.get(url__regex=r".*visa-bulletin-for-.*\.html$").mock(
        return_value=httpx.Response(200, text=bulletin_html))

    sender = RecordingSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    scraper = BulletinScraper(db=db, notifier=notifier)

    # First run: discovers everything
    await scraper.check_for_new_bulletins()
    sender.sent.clear()
    # Second run: nothing new
    result = await scraper.check_for_new_bulletins()
    assert result.new_bulletin_months == []
    assert sender.sent == []


@pytest.mark.asyncio
@respx.mock
async def test_network_error_logs_and_does_not_raise(tmp_path):
    respx.get(
        "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
    ).mock(side_effect=httpx.ConnectError("boom"))

    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    sender = RecordingSender()
    notifier = Notifier(db=db, sender=sender, priority_date=date(2018, 7, 3))
    scraper = BulletinScraper(db=db, notifier=notifier)

    result = await scraper.check_for_new_bulletins()
    assert result.error is not None
    runs = db.recent_scrape_runs(limit=1)
    assert runs[0].status == "error"
```

- [ ] **Step 2: Verify the tests fail**

Run: `pytest tests/test_scraper_orchestration.py -v`
Expected: ImportError on `BulletinScraper`.

- [ ] **Step 3: Extend `src/visa_tracker/scraper.py`**

Add at top:
```python
from dataclasses import field
import httpx
import logging

from visa_tracker.db import Database

log = logging.getLogger(__name__)

LISTING_URL = "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
USER_AGENT = "visa-tracker/1.0 (+personal use)"
HTTP_TIMEOUT = 20.0
```

Then at the bottom of the file, add:
```python
@dataclass
class ScrapeResult:
    new_bulletin_months: list[str] = field(default_factory=list)
    error: str | None = None


class BulletinScraper:
    def __init__(self, *, db: Database, notifier):
        self.db = db
        self.notifier = notifier

    async def check_for_new_bulletins(self) -> ScrapeResult:
        result = ScrapeResult()
        try:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
            ) as client:
                listing_html = (await client.get(LISTING_URL)).text
                available = list_available_bulletins(listing_html, base_url=LISTING_URL)
                known = set(self.db.all_bulletin_months())
                new_links = [b for b in available if b.bulletin_month not in known]
                # process oldest-first so notifications fire in order
                for link in sorted(new_links, key=lambda b: b.bulletin_month):
                    bulletin_html = (await client.get(link.url)).text
                    try:
                        parsed = parse_bulletin(bulletin_html)
                    except ValueError as e:
                        log.exception("parser failed for %s", link.url)
                        self.db.log_scrape_run(status="error",
                                               detail=f"parser_broken: {e}",
                                               bulletin_month=link.bulletin_month)
                        await self.notifier.handle_parser_broken(
                            link.bulletin_month, str(e))
                        continue
                    prev = self.db.previous_bulletin(link.bulletin_month)
                    self.db.insert_bulletin(link.bulletin_month, parsed,
                                             link.url, simulated=False)
                    new_row = self.db.get_bulletin(link.bulletin_month)
                    await self.notifier.handle_new_bulletin(new_row, prev)
                    result.new_bulletin_months.append(link.bulletin_month)
                    self.db.log_scrape_run(status="new_bulletin",
                                           bulletin_month=link.bulletin_month)
            if not result.new_bulletin_months:
                self.db.log_scrape_run(status="no_change")
        except Exception as e:
            log.exception("scrape failed")
            result.error = str(e)
            self.db.log_scrape_run(status="error", detail=str(e))
        return result
```

- [ ] **Step 4: Add `handle_parser_broken` to Notifier**

In `src/visa_tracker/notifier.py`, inside the `Notifier` class:
```python
async def handle_parser_broken(self, bulletin_month: str, detail: str) -> None:
    msg = (
        "⚠️ visa-tracker: parser failed\n\n"
        f"Bulletin: {bulletin_month}\n"
        f"Detail: {detail}\n\n"
        "Inspect the bulletin page and update the parser."
    )
    await self._send_once("parser_broken", bulletin_month, msg)
```

- [ ] **Step 5: Verify the tests pass**

Run: `pytest tests/test_scraper_orchestration.py tests/test_notifier.py -v`
Expected: all tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/visa_tracker/scraper.py src/visa_tracker/notifier.py tests/test_scraper_orchestration.py
git commit -m "feat(scraper): orchestration with diff, parser-broken alert, and run logging"
```

---

## Task 12: Web app (FastAPI routes + Jinja template)

**Files:**
- Create: `src/visa_tracker/web.py`
- Create: `src/visa_tracker/templates/dashboard.html`
- Create: `tests/test_web.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_web.py`:
```python
from datetime import date
from pathlib import Path
import pytest
from httpx import AsyncClient, ASGITransport
from visa_tracker.db import Database
from visa_tracker.web import create_app

SEED_PATH = Path(__file__).resolve().parents[1] / "seed.json"


@pytest.fixture
def seeded_db(tmp_path):
    db = Database(str(tmp_path / "w.db"))
    db.init_schema()
    db.load_seed_if_empty(SEED_PATH)
    return db


@pytest.mark.asyncio
async def test_healthz(seeded_db):
    app = create_app(db=seeded_db, priority_date=date(2018, 7, 3))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"


@pytest.mark.asyncio
async def test_api_data_shape(seeded_db):
    app = create_app(db=seeded_db, priority_date=date(2018, 7, 3))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/data")
    assert resp.status_code == 200
    body = resp.json()
    assert body["priority_date"] == "2018-07-03"
    assert isinstance(body["bulletins"], list)
    assert len(body["bulletins"]) == 12
    assert body["projection"] is not None
    assert {"bulletin_month", "final_action_date", "dates_for_filing"}.issubset(
        body["bulletins"][0].keys())


@pytest.mark.asyncio
async def test_dashboard_renders(seeded_db):
    app = create_app(db=seeded_db, priority_date=date(2018, 7, 3))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert "F2B Visa Tracker" in resp.text
    assert "2026-06" in resp.text or "Jun 2026" in resp.text
    assert "03-JUL-2018" in resp.text
```

- [ ] **Step 2: Verify the tests fail**

Run: `pytest tests/test_web.py -v`
Expected: ImportError on `visa_tracker.web`.

- [ ] **Step 3: Implement `src/visa_tracker/web.py`**

```python
from __future__ import annotations
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from visa_tracker.db import Database, BulletinRow
from visa_tracker.parsing import CURRENT_SENTINEL
from visa_tracker.projection import compute_projection, Projection


def create_app(*, db: Database, priority_date: date) -> FastAPI:
    app = FastAPI(title="visa-tracker")
    templates_dir = Path(__file__).parent / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))

    def _serialize_bulletin(b: BulletinRow) -> dict:
        return {
            "bulletin_month": b.bulletin_month,
            "final_action_date": _iso_or_sentinel(b.final_action_date),
            "dates_for_filing": _iso_or_sentinel(b.dates_for_filing),
            "raw_final_action": b.raw_final_action,
            "raw_dates_filing": b.raw_dates_filing,
            "source_url": b.source_url,
            "fetched_at": b.fetched_at.isoformat(),
        }

    def _serialize_projection(p: Projection) -> dict:
        def rng(r):
            return None if r is None else [r[0].isoformat(), r[1].isoformat()]
        return {
            "pd_is_current_final": p.pd_is_current_final,
            "pd_is_current_filing": p.pd_is_current_filing,
            "final_action_eta_range": rng(p.final_action_eta_range),
            "filing_eta_range": rng(p.filing_eta_range),
            "days_per_month_final_recent": p.days_per_month_final_recent,
            "days_per_month_final_year": p.days_per_month_final_year,
            "days_per_month_filing_recent": p.days_per_month_filing_recent,
            "days_per_month_filing_year": p.days_per_month_filing_year,
        }

    def _payload() -> dict:
        history = db.all_bulletins()
        proj = compute_projection(history, priority_date)
        runs = db.recent_scrape_runs(limit=1)
        last_check = runs[0].started_at.isoformat() if runs else None
        return {
            "priority_date": priority_date.isoformat(),
            "bulletins": [_serialize_bulletin(b) for b in history],
            "projection": _serialize_projection(proj),
            "last_check": last_check,
        }

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

    @app.get("/api/data")
    async def api_data():
        return JSONResponse(_payload())

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        data = _payload()
        return templates.TemplateResponse(
            "dashboard.html",
            {"request": request, **data, "priority_date_display": priority_date.strftime("%d-%b-%Y").upper()},
        )

    return app


def _iso_or_sentinel(d: date | None) -> str | None:
    if d is None:
        return None
    if d == CURRENT_SENTINEL:
        return "C"
    return d.isoformat()
```

- [ ] **Step 4: Implement `src/visa_tracker/templates/dashboard.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>F2B Visa Tracker — Armenia</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root { --bg:#0f1115; --panel:#161a22; --border:#242a36; --text:#e8ecf3;
          --muted:#8a93a6; --accent:#5aa9ff; --accent2:#ffb86b; --good:#4ade80; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--text); line-height:1.5; }
  .wrap { max-width:1100px; margin:0 auto; padding:32px 24px 80px; }
  h1 { font-size:28px; margin:0 0 4px; }
  .sub { color:var(--muted); margin-bottom:24px; }
  .kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin-bottom:24px; }
  .kpi { background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:14px 16px; }
  .kpi .label { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.05em; }
  .kpi .value { font-size:20px; font-weight:600; margin-top:4px; }
  .panel { background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:20px; margin-bottom:20px; }
  .panel h2 { margin:0 0 12px; font-size:18px; }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); }
  th { color:var(--muted); font-weight:500; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  td.num { font-variant-numeric:tabular-nums; }
  .chart-wrap { position:relative; height:380px; }
  .footnote { color:var(--muted); font-size:12px; margin-top:16px; }
  .delta-pos { color:var(--good); }
  .delta-neg { color:#f87171; }
  .delta-zero { color:var(--muted); }
  a { color:var(--accent); }
</style>
</head>
<body>
<div class="wrap">
  <h1>F2B Visa Tracker — Armenia</h1>
  <div class="sub">
    Priority date: <strong>{{ priority_date_display }}</strong>
    {% if last_check %}· Last checked: {{ last_check }}{% endif %}
  </div>

  <div id="kpis" class="kpis"></div>

  <div class="panel">
    <h2>Cutoff date over time</h2>
    <div class="chart-wrap"><canvas id="trendChart"></canvas></div>
    <div class="footnote">Dashed line = your priority date.</div>
  </div>

  <div class="panel">
    <h2>Projection for your priority date</h2>
    <div id="projection"></div>
  </div>

  <div class="panel">
    <h2>Month-by-month history</h2>
    <table id="dataTable">
      <thead>
        <tr><th>Bulletin</th><th>Final Action</th><th>Δ days</th><th>Dates for Filing</th><th>Δ days</th></tr>
      </thead>
      <tbody></tbody>
    </table>
  </div>

  <div class="footnote">
    Source:
    <a href="https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html">travel.state.gov</a>
  </div>
</div>

<script id="payload" type="application/json">{{ {"bulletins": bulletins, "priority_date": priority_date, "projection": projection} | tojson }}</script>
<script>
  const payload = JSON.parse(document.getElementById("payload").textContent);
  const data = payload.bulletins.slice().sort((a,b) => a.bulletin_month.localeCompare(b.bulletin_month));
  const pdISO = payload.priority_date;

  // KPIs
  const latest = data[data.length - 1];
  const prev = data[data.length - 2] || null;
  const dayDelta = (a,b) => Math.round((new Date(b) - new Date(a)) / 86400000);
  const fmt = iso => iso ? new Date(iso).toLocaleDateString("en-US",{day:"2-digit",month:"short",year:"numeric"}) : "—";
  const kpis = document.getElementById("kpis");
  const gapFinal = latest.final_action_date ? Math.round((new Date(pdISO) - new Date(latest.final_action_date))/86400000) : null;
  const gapFiling = latest.dates_for_filing ? Math.round((new Date(pdISO) - new Date(latest.dates_for_filing))/86400000) : null;
  function card(label, value, sub) {
    return `<div class="kpi"><div class="label">${label}</div><div class="value">${value}</div><div class="footnote">${sub||""}</div></div>`;
  }
  const dF = prev && latest.final_action_date && prev.final_action_date ? dayDelta(prev.final_action_date, latest.final_action_date) : null;
  const dD = prev && latest.dates_for_filing && prev.dates_for_filing ? dayDelta(prev.dates_for_filing, latest.dates_for_filing) : null;
  kpis.innerHTML = [
    card("Latest Final Action", fmt(latest.final_action_date), dF !== null ? `${dF >= 0 ? "+" : ""}${dF} days vs prev` : ""),
    card("Latest Dates for Filing", fmt(latest.dates_for_filing), dD !== null ? `${dD >= 0 ? "+" : ""}${dD} days vs prev` : ""),
    card("Gap to Final Action", gapFinal !== null ? `${(gapFinal/30).toFixed(1)} months` : "—", "behind your PD"),
    card("Gap to Filing", gapFiling !== null ? `${(gapFiling/30).toFixed(1)} months` : "—", "behind your PD"),
  ].join("");

  // Projection
  const proj = payload.projection;
  const projDiv = document.getElementById("projection");
  function rng(r) { return r ? `${fmt(r[0])} → ${fmt(r[1])}` : "—"; }
  projDiv.innerHTML = `
    <div>Filing chart current: <strong>${proj.pd_is_current_filing ? "✅ already current" : rng(proj.filing_eta_range)}</strong></div>
    <div>Final Action current: <strong>${proj.pd_is_current_final ? "✅ already current" : rng(proj.final_action_eta_range)}</strong></div>
    <div class="footnote">Pace: ${proj.days_per_month_final_recent.toFixed(0)} d/mo (recent 3), ${proj.days_per_month_final_year.toFixed(0)} d/mo (last 12).</div>
  `;

  // Table (newest first)
  const tbody = document.querySelector("#dataTable tbody");
  const dataNewestFirst = data.slice().reverse();
  dataNewestFirst.forEach((row, i) => {
    const nextRow = dataNewestFirst[i+1]; // older
    const fa = row.final_action_date && nextRow && nextRow.final_action_date ? dayDelta(nextRow.final_action_date, row.final_action_date) : null;
    const df = row.dates_for_filing && nextRow && nextRow.dates_for_filing ? dayDelta(nextRow.dates_for_filing, row.dates_for_filing) : null;
    const cls = v => v == null ? "" : (v > 0 ? "delta-pos" : v < 0 ? "delta-neg" : "delta-zero");
    const txt = v => v == null ? "—" : (v > 0 ? `+${v}` : `${v}`);
    tbody.insertAdjacentHTML("beforeend", `<tr>
      <td>${row.bulletin_month}</td>
      <td class="num">${fmt(row.final_action_date)}</td>
      <td class="num ${cls(fa)}">${txt(fa)}</td>
      <td class="num">${fmt(row.dates_for_filing)}</td>
      <td class="num ${cls(df)}">${txt(df)}</td>
    </tr>`);
  });

  // Chart
  const ctx = document.getElementById("trendChart").getContext("2d");
  new Chart(ctx, {
    type: "line",
    data: {
      labels: data.map(d => d.bulletin_month),
      datasets: [
        { label: "Final Action", data: data.map(d => d.final_action_date ? new Date(d.final_action_date).getTime() : null),
          borderColor: "#5aa9ff", backgroundColor: "rgba(90,169,255,0.15)", tension:0.25, pointRadius:4, fill:true },
        { label: "Dates for Filing", data: data.map(d => d.dates_for_filing ? new Date(d.dates_for_filing).getTime() : null),
          borderColor: "#ffb86b", backgroundColor: "rgba(255,184,107,0.10)", tension:0.25, pointRadius:4, fill:false },
        { label: "Your PD", data: data.map(_ => new Date(pdISO).getTime()),
          borderColor: "#4ade80", borderDash: [6,4], pointRadius:0, fill:false }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: "#e8ecf3" } },
        tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${new Date(ctx.parsed.y).toLocaleDateString("en-US",{day:"2-digit",month:"short",year:"numeric"})}` } }
      },
      scales: {
        x: { ticks: { color: "#8a93a6" }, grid: { color: "#242a36" } },
        y: { type: "linear", ticks: { color: "#8a93a6", callback: v => new Date(v).toLocaleDateString("en-US",{month:"short",year:"numeric"}) }, grid: { color: "#242a36" } }
      }
    }
  });
</script>
</body>
</html>
```

- [ ] **Step 5: Verify the tests pass**

Run: `pytest tests/test_web.py -v`
Expected: 3 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/visa_tracker/web.py src/visa_tracker/templates/dashboard.html tests/test_web.py
git commit -m "feat(web): FastAPI dashboard with KPIs, chart, history table, projection"
```

---

## Task 13: Scheduler

**Files:**
- Create: `src/visa_tracker/scheduler.py`

- [ ] **Step 1: Implement `src/visa_tracker/scheduler.py`**

This module is thin glue — APScheduler is a library; we don't unit-test it. Instead we verify by running the app.

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add src/visa_tracker/scheduler.py
git commit -m "feat(scheduler): APScheduler cron jobs for smart polling window"
```

---

## Task 14: CLI subcommands (dry-run + previews)

**Files:**
- Create: `src/visa_tracker/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:
```python
from datetime import date
from pathlib import Path
import pytest
from visa_tracker.cli import render_message_for_bulletin
from visa_tracker.db import Database

SEED_PATH = Path(__file__).resolve().parents[1] / "seed.json"


def test_render_message_for_existing_bulletin(tmp_path):
    db = Database(str(tmp_path / "c.db"))
    db.init_schema()
    db.load_seed_if_empty(SEED_PATH)
    out = render_message_for_bulletin(db, "2026-06", priority_date=date(2018, 7, 3))
    assert "June 2026" in out
    assert "22-SEP-2017" in out


def test_render_message_unknown_bulletin_raises(tmp_path):
    db = Database(str(tmp_path / "c.db"))
    db.init_schema()
    with pytest.raises(KeyError):
        render_message_for_bulletin(db, "1999-01", priority_date=date(2018, 7, 3))
```

- [ ] **Step 2: Verify the tests fail**

Run: `pytest tests/test_cli.py -v`
Expected: ImportError on `visa_tracker.cli`.

- [ ] **Step 3: Implement `src/visa_tracker/cli.py`**

```python
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
        from copy import copy
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
```

- [ ] **Step 4: Verify the tests pass**

Run: `pytest tests/test_cli.py -v`
Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/visa_tracker/cli.py tests/test_cli.py
git commit -m "feat(cli): dry-run, simulate-bulletin, render-message, test-telegram subcommands"
```

---

## Task 15: Application entry point

**Files:**
- Create: `src/visa_tracker/__main__.py`

- [ ] **Step 1: Implement the entry point**

```python
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


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("visa_tracker")

    db = Database(settings.db_path)
    db.init_schema()
    inserted = db.load_seed_if_empty(_seed_path())
    if inserted:
        log.info("seeded %d bulletin rows from seed.json", inserted)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    notifier = loop.run_until_complete(_build_notifier(db, settings))
    scraper = BulletinScraper(db=db, notifier=notifier)
    scheduler = start_scheduler(scraper, timezone=os.environ.get("TZ", "UTC"))

    app = create_app(db=db, priority_date=settings.priority_date)
    host, port = settings.web_bind.split(":")
    config = uvicorn.Config(app=app, host=host, port=int(port),
                              log_level=settings.log_level.lower(), loop="asyncio")
    server = uvicorn.Server(config)

    def _shutdown(*_):
        log.info("shutdown signal received")
        scheduler.shutdown(wait=False)
        server.should_exit = True

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log.info("starting web on %s", settings.web_bind)
    loop.run_until_complete(server.serve())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test it locally**

```bash
PRIORITY_DATE=2018-07-03 DB_PATH=./data/tracker.db WEB_BIND=127.0.0.1:8080 \
  python -m visa_tracker &
sleep 2
curl -s http://127.0.0.1:8080/healthz
curl -s http://127.0.0.1:8080/api/data | python -c "import json,sys; d=json.load(sys.stdin); print(len(d['bulletins']), d['priority_date'])"
kill %1
```

Expected output: `ok`, then `12 2018-07-03`.

- [ ] **Step 3: Commit**

```bash
git add src/visa_tracker/__main__.py
git commit -m "feat(main): app entry point — db init, scheduler, FastAPI server, signal handlers"
```

---

## Task 16: Dockerfile + docker-compose + .env.example

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.env.example`

- [ ] **Step 1: Write `Dockerfile`**

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

- [ ] **Step 2: Write `docker-compose.yml`**

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

- [ ] **Step 3: Write `.env.example`**

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

- [ ] **Step 4: Build and verify**

```bash
docker compose build
docker compose up -d
sleep 5
curl -s http://127.0.0.1:8080/healthz
curl -s http://127.0.0.1:8080/api/data | python -c "import json,sys; d=json.load(sys.stdin); print(len(d['bulletins']))"
docker compose down
```

Expected: `ok`, then `12`.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml .env.example
git commit -m "build: dockerfile, docker-compose, env example"
```

---

## Task 17: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# visa-tracker

A personal service that monitors the U.S. State Department visa bulletin (F2B category, "All Chargeability Areas" column — applies to Armenia) and sends Telegram notifications when new bulletins are published or when the priority date `2018-07-03` becomes current.

## Quick start

```bash
git clone <repo>
cd visa-tracker
cp .env.example .env       # optional: fill in Telegram bot token + chat ID
docker compose up -d
open http://localhost:8080
```

The dashboard ships with 12 months of historical data and starts polling for new bulletins automatically.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | empty | Bot token from @BotFather. Leave empty to disable Telegram. |
| `TELEGRAM_CHAT_ID` | empty | Your chat ID (see "Telegram setup" below). |
| `TZ` | `UTC` | Used for cron interpretation. |
| `LOG_LEVEL` | `INFO` | `DEBUG` for diagnosis. |
| `WEB_BIND` | `0.0.0.0:8080` | Listen address inside the container. |
| `PRIORITY_DATE` | `2018-07-03` | Hardcoded priority date to track. |

## Telegram setup

1. Talk to `@BotFather` in Telegram, run `/newbot`, get the bot token.
2. Send any message to your new bot.
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and find your `chat.id`.
4. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
5. Verify: `docker compose exec visa-tracker python -m visa_tracker.cli test-telegram`

## CLI utilities

All run inside the container via `docker compose exec visa-tracker python -m visa_tracker.cli <cmd>`:

- `check-now` — force one scrape cycle now (writes + notifies as normal).
- `check-now --dry-run` — preview what *would* happen, no writes/sends.
- `simulate-bulletin --month 2026-07 --final-action 2017-10-22 --filing 2018-04-15 [--dry-run]` — inject a hypothetical bulletin.
- `render-message <YYYY-MM>` — print the Telegram message body for an existing bulletin.
- `test-telegram` — send a connectivity test.

## Development

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README with quick start, config, telegram setup, CLI utilities"
```

---

## Task 18: End-to-end smoke test (manual)

- [ ] **Step 1: Run the full suite**

```bash
pytest -v
```

Expected: all tests pass, no warnings about unclosed event loops.

- [ ] **Step 2: docker compose build + up**

```bash
docker compose build
docker compose up -d
```

- [ ] **Step 3: Verify dashboard**

```bash
sleep 5
curl -s http://127.0.0.1:8080/healthz       # expect: ok
open http://127.0.0.1:8080                  # browser: KPIs, chart, history, projection
```

- [ ] **Step 4: Verify dry-run CLI**

```bash
docker compose exec visa-tracker python -m visa_tracker.cli check-now --dry-run
```

Expected: "no new bulletins" or a printed Telegram preview of any newly-published bulletin.

- [ ] **Step 5: Verify simulate-bulletin**

```bash
docker compose exec visa-tracker python -m visa_tracker.cli simulate-bulletin \
  --month 2026-12 --final-action 2018-08-01 --filing 2018-09-01 --dry-run
```

Expected: printed "would send Telegram" message showing PD-current alert (final action crossing 2018-07-03).

- [ ] **Step 6: Tear down**

```bash
docker compose down
```

- [ ] **Step 7: Final commit if any tweaks were needed**

```bash
git status
# if anything modified during smoke test, commit it:
git add -A
git commit -m "chore: smoke test fixes"
```
