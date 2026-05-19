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
            request,
            "dashboard.html",
            {**data, "priority_date_display": priority_date.strftime("%d-%b-%Y").upper()},
        )

    return app


def _iso_or_sentinel(d: date | None) -> str | None:
    if d is None:
        return None
    if d == CURRENT_SENTINEL:
        return "C"
    return d.isoformat()
