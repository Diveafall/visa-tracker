from datetime import date
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
