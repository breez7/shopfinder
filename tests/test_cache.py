from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import respx
from httpx import ASGITransport
from sqlmodel import Session, select

from app.adapters.naver import NAVER_SEARCH_URL
from app.adapters.types import ParsedConditions, SearchResult
from app.cache import (
    DEFAULT_TTL_HOURS,
    conditions_hash,
    load,
    purge_expired,
    store,
)
from app.db.models import SearchResultsCache, Shop
from app.db.session import engine
from app.main import app


def _truncate_cache() -> None:
    with Session(engine) as session:
        for row in session.exec(select(SearchResultsCache)).all():
            session.delete(row)
        session.commit()


def test_conditions_hash_is_deterministic_and_strips_override() -> None:
    a = ParsedConditions(color="검정", category="남방", max_price=20000)
    b = ParsedConditions(color="검정", category="남방", max_price=20000, keyword_override="ignored")
    assert conditions_hash(a) == conditions_hash(b)


def test_conditions_hash_differs_between_distinct_conditions() -> None:
    a = ParsedConditions(color="검정")
    b = ParsedConditions(color="흰색")
    assert conditions_hash(a) != conditions_hash(b)


def test_store_and_load_round_trip() -> None:
    _truncate_cache()
    key = "abc"
    results = [SearchResult(shop_slug="x", title="t", product_url="u", price=1000)]
    store(key, results)
    loaded = load(key)
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0].title == "t"
    assert loaded[0].price == 1000


def test_load_returns_none_when_missing() -> None:
    _truncate_cache()
    assert load("missing-key") is None


def test_load_returns_none_when_expired() -> None:
    _truncate_cache()
    key = "expired"
    results = [SearchResult(shop_slug="x", title="t", product_url="u")]
    # Insert with already-past expires_at
    with Session(engine) as session:
        session.add(
            SearchResultsCache(
                conditions_hash=key,
                payload_json="[]",
                expires_at=datetime.utcnow() - timedelta(hours=1),
            )
        )
        session.commit()
    assert load(key) is None
    # Row was lazily pruned
    with Session(engine) as session:
        rows = session.exec(
            select(SearchResultsCache).where(SearchResultsCache.conditions_hash == key)
        ).all()
        assert rows == []


def test_store_updates_existing_row() -> None:
    _truncate_cache()
    key = "upd"
    store(key, [SearchResult(shop_slug="x", title="first", product_url="u")])
    store(key, [SearchResult(shop_slug="x", title="second", product_url="u")])
    loaded = load(key)
    assert loaded is not None
    assert loaded[0].title == "second"


def test_purge_expired_removes_old_rows() -> None:
    _truncate_cache()
    with Session(engine) as session:
        session.add(
            SearchResultsCache(
                conditions_hash="old",
                payload_json="[]",
                expires_at=datetime.utcnow() - timedelta(hours=1),
            )
        )
        session.add(
            SearchResultsCache(
                conditions_hash="new",
                payload_json="[]",
                expires_at=datetime.utcnow() + timedelta(hours=1),
            )
        )
        session.commit()
    removed = purge_expired()
    assert removed == 1


def _enable_only_naver() -> None:
    with Session(engine) as session:
        for s in session.exec(select(Shop)).all():
            s.enabled = s.slug == "naver"
            session.add(s)
        session.commit()


@respx.mock
async def test_second_search_hits_cache(monkeypatch) -> None:
    """First search calls Naver; second search with same query returns from cache."""
    _truncate_cache()
    _enable_only_naver()
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")

    naver_route = respx.get(NAVER_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"title": "t", "link": "https://x/p", "lprice": "1000"}]},
        )
    )

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/search/stream?q=검정 남방") as r1:
            async for _ in r1.aiter_text():
                pass

    first_call_count = naver_route.call_count
    assert first_call_count >= 1

    # Second hit — Naver should NOT be called again
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        from_cache_flag = False
        async with client.stream("GET", "/search/stream?q=검정 남방") as r2:
            async for line in r2.aiter_lines():
                if "from_cache" in line and "true" in line.lower():
                    from_cache_flag = True
        assert from_cache_flag

    # No additional Naver call
    assert naver_route.call_count == first_call_count


@respx.mock
async def test_force_refresh_bypasses_cache(monkeypatch) -> None:
    _truncate_cache()
    _enable_only_naver()
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")

    naver_route = respx.get(NAVER_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"title": "t", "link": "https://x/p", "lprice": "1000"}]},
        )
    )

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/search/stream?q=검정 남방") as r1:
            async for _ in r1.aiter_text():
                pass

    first = naver_route.call_count

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/search/stream?q=검정 남방&refresh=1") as r2:
            async for _ in r2.aiter_text():
                pass

    assert naver_route.call_count == first + 1
