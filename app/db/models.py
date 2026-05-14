from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str = ""


class Shop(SQLModel, table=True):
    __tablename__ = "shops"

    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    adapter_module: str
    enabled: bool = True
    config_json: str = "{}"


class SearchHistory(SQLModel, table=True):
    __tablename__ = "search_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_query: str
    parsed_conditions_json: str = "{}"
    parsed_by: str = "regex"
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    total_results: int = 0
    elapsed_ms: int = 0


class SearchResultsCache(SQLModel, table=True):
    __tablename__ = "search_results_cache"

    id: Optional[int] = Field(default=None, primary_key=True)
    conditions_hash: str = Field(unique=True, index=True)
    payload_json: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime


class ClickLog(SQLModel, table=True):
    __tablename__ = "click_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    search_history_id: int = Field(foreign_key="search_history.id", index=True)
    shop_slug: str
    result_url: str
    clicked_at: datetime = Field(default_factory=datetime.utcnow)


class SchemaVersion(SQLModel, table=True):
    __tablename__ = "schema_version"

    version: int = Field(primary_key=True)
    applied_at: datetime = Field(default_factory=datetime.utcnow)


class AdapterWarning(SQLModel, table=True):
    __tablename__ = "adapter_warnings"

    id: Optional[int] = Field(default=None, primary_key=True)
    shop_slug: str = Field(index=True)
    kind: str
    message: str
    snippet: str = ""
    raised_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    dismissed: bool = False
