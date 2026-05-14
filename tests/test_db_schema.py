from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import (
    AdapterWarning,
    ClickLog,
    SchemaVersion,
    SearchHistory,
    SearchResultsCache,
    Setting,
    Shop,
)
from app.db.seed import CURRENT_SCHEMA_VERSION, DEFAULT_SHOPS, seed_defaults


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_seed_inserts_five_default_shops(session: Session) -> None:
    seed_defaults(session)

    slugs = set(session.exec(select(Shop.slug)).all())
    assert slugs == {"naver", "coupang", "eleventh", "gmarket", "musinsa"}
    assert len(DEFAULT_SHOPS) == 5


def test_seed_is_idempotent(session: Session) -> None:
    seed_defaults(session)
    seed_defaults(session)
    seed_defaults(session)

    shops = session.exec(select(Shop)).all()
    assert len(shops) == 5
    versions = session.exec(select(SchemaVersion)).all()
    assert [v.version for v in versions] == [CURRENT_SCHEMA_VERSION]


def test_only_naver_enabled_by_default(session: Session) -> None:
    seed_defaults(session)
    enabled = session.exec(select(Shop).where(Shop.enabled == True)).all()  # noqa: E712
    assert [s.slug for s in enabled] == ["naver"]


def test_settings_crud(session: Session) -> None:
    session.add(Setting(key="llm_base_url", value="http://lan:1234/v1"))
    session.commit()
    fetched = session.get(Setting, "llm_base_url")
    assert fetched is not None
    assert fetched.value == "http://lan:1234/v1"


def test_search_history_crud(session: Session) -> None:
    row = SearchHistory(
        raw_query="검정 100 남방",
        parsed_conditions_json='{"color":"black"}',
        parsed_by="regex",
        total_results=12,
        elapsed_ms=4321,
    )
    session.add(row)
    session.commit()
    assert row.id is not None
    fetched = session.get(SearchHistory, row.id)
    assert fetched is not None
    assert fetched.total_results == 12


def test_click_log_links_to_search_history(session: Session) -> None:
    hist = SearchHistory(raw_query="x")
    session.add(hist)
    session.commit()
    click = ClickLog(
        search_history_id=hist.id,
        shop_slug="naver",
        result_url="https://example.com/p/1",
    )
    session.add(click)
    session.commit()
    assert click.id is not None


def test_cache_crud(session: Session) -> None:
    row = SearchResultsCache(
        conditions_hash="abc",
        payload_json="[]",
        expires_at=datetime.utcnow() + timedelta(hours=24),
    )
    session.add(row)
    session.commit()
    assert row.id is not None


def test_adapter_warning_crud(session: Session) -> None:
    w = AdapterWarning(shop_slug="coupang", kind="parse_exception", message="x")
    session.add(w)
    session.commit()
    assert w.id is not None
