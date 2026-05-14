from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SHOPFINDER_",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8080
    db_path: Path = Path("./data/shopfinder.db")
    secret_key: str = "change-me"

    @property
    def sqlite_url(self) -> str:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.db_path.resolve()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
