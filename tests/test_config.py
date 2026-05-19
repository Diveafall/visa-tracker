from datetime import date
from visa_tracker.config import Settings


def test_defaults_when_no_env(monkeypatch):
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                "LOG_LEVEL", "WEB_BIND", "PRIORITY_DATE", "DB_PATH"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
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
    s = Settings(_env_file=None)
    assert s.telegram_bot_token == "abc:123"
    assert s.telegram_chat_id == "987654"
    assert s.priority_date == date(2019, 1, 15)


def test_telegram_enabled(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert Settings(_env_file=None).telegram_enabled is False
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    assert Settings(_env_file=None).telegram_enabled is True
