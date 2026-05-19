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
