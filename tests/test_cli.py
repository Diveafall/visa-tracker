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
