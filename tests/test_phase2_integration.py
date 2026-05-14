"""Phase 2 integration test (issue #31).

Hits the live FastAPI app with all 5 adapters enabled and:
- Naver Open API mocked via respx
- 4 HTML adapters served real-looking HTML fixtures
- An OpenAI-compatible LLM server stubbed by respx that dispatches
  responses based on the request payload (parse / optimize / score)

Covers Phase 2 FRs: FR-2 (LLM path), FR-4 (all 5 adapters), FR-5,
FR-6 (score + reason on cards), FR-7 (markup contract for sort/filter),
FR-9 (settings reads at runtime), FR-13 (24h cache + force refresh),
plus #21 warning logs.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from httpx import ASGITransport
from sqlmodel import Session, select

from app.adapters.coupang import CoupangAdapter
from app.adapters.eleventh import ElevenstAdapter
from app.adapters.gmarket import GmarketAdapter
from app.adapters.musinsa import MusinsaAdapter
from app.adapters.naver import NAVER_SEARCH_URL
from app.db.models import (
    AdapterWarning,
    ClickLog,
    SearchHistory,
    SearchResultsCache,
    Setting,
    Shop,
)
from app.db.session import engine
from app.main import app
from app.settings_store import (
    KEY_LLM_API_KEY,
    KEY_LLM_BASE_URL,
    KEY_LLM_MODEL,
    set_ as settings_set,
)

FIXTURES = Path(__file__).parent / "fixtures"

LLM_BASE = "https://llm.example/v1"


def _reset_state() -> None:
    from app.shops_admin import BUILTIN_SLUGS

    with Session(engine) as session:
        for tbl in (
            ClickLog, SearchHistory, SearchResultsCache, AdapterWarning, Setting
        ):
            for row in session.exec(select(tbl)).all():
                session.delete(row)
        for shop in session.exec(select(Shop)).all():
            if shop.slug not in BUILTIN_SLUGS:
                session.delete(shop)
            else:
                shop.enabled = True
                session.add(shop)
        session.commit()


def _configure_llm() -> None:
    with Session(engine) as session:
        settings_set(session, KEY_LLM_BASE_URL, LLM_BASE)
        settings_set(session, KEY_LLM_API_KEY, "sk-test")
        settings_set(session, KEY_LLM_MODEL, "test-model")


def _llm_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def _llm_dispatch(request: httpx.Request) -> httpx.Response:
    """Inspect the system prompt to decide what canned response to return."""
    body = request.content.decode("utf-8")
    try:
        payload = json.loads(body)
    except ValueError:
        return _llm_response("{}")

    system_msg = ""
    for msg in payload.get("messages", []):
        if msg.get("role") == "system":
            system_msg = msg.get("content", "")
            break

    if "extract structured shopping conditions" in system_msg:
        # Parser
        return _llm_response(
            '{"category":"긴팔 남방","color":"검정","size":"100","material":"폴리에스테르",'
            '"material_pct":80,"fit":"루즈핏","max_price":20000,"free_text":""}'
        )
    if "compress structured shopping conditions" in system_msg:
        # Query optimizer
        return _llm_response("검정 남방 루즈")
    if "evaluate shopping search results" in system_msg:
        # Match scorer — parse user message to count items in the batch
        user_msg = next(
            (m.get("content", "") for m in payload.get("messages", []) if m.get("role") == "user"),
            "",
        )
        item_count = user_msg.count("\n") + 1 if "Items:" in user_msg else 1
        # Just return a generic score+reason per item
        entries = [
            {"index": i, "score": 75 + (i % 5), "reason": "조건 일부 매칭"}
            for i in range(20)
        ]
        return _llm_response(json.dumps(entries[:item_count]))
    return _llm_response("{}")


def _mount_all_mocks(monkeypatch) -> None:
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    # Speed up HTML adapter random jitter
    for cls in (CoupangAdapter, ElevenstAdapter, GmarketAdapter, MusinsaAdapter):
        cls.min_delay_s = 0
        cls.max_delay_s = 0

    # Naver Open API
    respx.get(NAVER_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "title": "Naver 검정 폴리 남방",
                        "link": "https://shopping.naver.com/p/1",
                        "image": "https://img/1.jpg",
                        "lprice": "15000",
                    }
                ]
            },
        )
    )

    # Four HTML adapters — match by base host
    respx.get(url__regex=r"https?://(www\.)?coupang\.com/np/search.*").mock(
        return_value=httpx.Response(
            200, text=(FIXTURES / "coupang_sample.html").read_text(encoding="utf-8")
        )
    )
    respx.get(url__regex=r"https?://search\.11st\.co\.kr/.*").mock(
        return_value=httpx.Response(
            200, text=(FIXTURES / "eleventh_sample.html").read_text(encoding="utf-8")
        )
    )
    respx.get(url__regex=r"https?://browse\.gmarket\.co\.kr/.*").mock(
        return_value=httpx.Response(
            200, text=(FIXTURES / "gmarket_sample.html").read_text(encoding="utf-8")
        )
    )
    respx.get(url__regex=r"https?://www\.musinsa\.com/search/.*").mock(
        return_value=httpx.Response(
            200, text=(FIXTURES / "musinsa_sample.html").read_text(encoding="utf-8")
        )
    )

    # LLM — dispatcher
    respx.post(f"{LLM_BASE}/chat/completions").mock(side_effect=_llm_dispatch)


async def _drain_sse(url: str) -> dict:
    """Run a SSE request and return parsed events."""
    transport = ASGITransport(app=app)
    events = {
        "meta": [],
        "shop_started": [],
        "shop_completed": [],
        "shop_failed": [],
        "result": [],
        "score_update": [],
        "done": False,
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", url) as response:
            assert response.status_code == 200
            current = None
            buf: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    current = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    buf.append(line[5:].strip())
                elif line == "" and current:
                    data = "\n".join(buf)
                    if current == "done":
                        events["done"] = True
                    elif current in events:
                        events[current].append(data)
                    current = None
                    buf = []
    return events


PRD_QUERY = "검정색 100 사이즈 긴팔 남방 폴리에스테르 80 이상 루즈핏 2만원 이하"


@respx.mock
async def test_phase2_five_shops_plus_llm_full_lifecycle(monkeypatch) -> None:
    _reset_state()
    _configure_llm()
    _mount_all_mocks(monkeypatch)

    events = await _drain_sse(f"/search/stream?q={PRD_QUERY}")

    assert events["done"]
    # All 5 shop slugs appear in shop_started
    started_slugs = {json.loads(d)["slug"] for d in events["shop_started"]}
    assert started_slugs == {"naver", "coupang", "eleventh", "gmarket", "musinsa"}
    # Each shop yields ≥1 result against its fixture
    result_shops = set()
    for raw in events["result"]:
        for shop in started_slugs:
            if f'data-shop="{shop}"' in raw:
                result_shops.add(shop)
    assert result_shops == started_slugs

    # FR-2: parsed_by stamped 'llm' (LLM mocked)
    with Session(engine) as session:
        hist = session.exec(
            select(SearchHistory).order_by(SearchHistory.created_at.desc())
        ).first()
        assert hist is not None
        assert hist.parsed_by.startswith("llm")

    # FR-6: every result has score_update emitted with reason ≤40 chars
    assert len(events["score_update"]) == len(events["result"])
    for raw in events["score_update"]:
        d = json.loads(raw)
        assert "score" in d
        assert "reason" in d
        assert d["reason"] is None or len(d["reason"]) <= 40


@respx.mock
async def test_phase2_cache_hit_skips_adapters_and_llm(monkeypatch) -> None:
    _reset_state()
    _configure_llm()
    _mount_all_mocks(monkeypatch)

    # Prime the cache
    await _drain_sse(f"/search/stream?q={PRD_QUERY}")
    first_naver_calls = respx.routes[0].call_count

    # Second identical search
    events = await _drain_sse(f"/search/stream?q={PRD_QUERY}")
    assert events["done"]
    # Naver should NOT be called again
    assert respx.routes[0].call_count == first_naver_calls
    # meta should mark from_cache=true
    meta = json.loads(events["meta"][0])
    assert meta["from_cache"] is True


@respx.mock
async def test_phase2_force_refresh_bypasses_cache(monkeypatch) -> None:
    _reset_state()
    _configure_llm()
    _mount_all_mocks(monkeypatch)

    await _drain_sse(f"/search/stream?q={PRD_QUERY}")
    n1 = respx.routes[0].call_count

    events = await _drain_sse(f"/search/stream?q={PRD_QUERY}&refresh=1")
    assert events["done"]
    assert respx.routes[0].call_count == n1 + 1
    meta = json.loads(events["meta"][0])
    assert meta["from_cache"] is False


@respx.mock
async def test_phase2_llm_unreachable_falls_back_gracefully(monkeypatch) -> None:
    """If LLM stops responding after settings save, parser falls back to
    regex, optimizer falls back to naive keyword, scorer skips entries."""
    _reset_state()
    _configure_llm()
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    for cls in (CoupangAdapter, ElevenstAdapter, GmarketAdapter, MusinsaAdapter):
        cls.min_delay_s = 0
        cls.max_delay_s = 0

    # LLM endpoint refuses all calls
    respx.post(f"{LLM_BASE}/chat/completions").mock(
        return_value=httpx.Response(500, text="llm down")
    )
    # Naver still works
    respx.get(NAVER_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"title": "t", "link": "https://x/p", "lprice": "1000"}]},
        )
    )
    # Other 4 sites return empty
    respx.get(url__regex=r"https?://(www\.)?coupang\.com/.*").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )
    respx.get(url__regex=r"https?://search\.11st\.co\.kr/.*").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )
    respx.get(url__regex=r"https?://browse\.gmarket\.co\.kr/.*").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )
    respx.get(url__regex=r"https?://www\.musinsa\.com/.*").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )

    events = await _drain_sse(f"/search/stream?q={PRD_QUERY}")
    assert events["done"]

    # Parser fell back to regex
    with Session(engine) as session:
        hist = session.exec(
            select(SearchHistory).order_by(SearchHistory.created_at.desc())
        ).first()
        assert hist is not None
        assert hist.parsed_by.startswith("regex")

    # No score_update emitted (scorer skipped because LLM is down)
    assert events["score_update"] == []
    # But there ARE results from Naver
    assert len(events["result"]) >= 1


@respx.mock
async def test_phase2_bot_detection_records_warning(monkeypatch) -> None:
    _reset_state()
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    for cls in (CoupangAdapter, ElevenstAdapter, GmarketAdapter, MusinsaAdapter):
        cls.min_delay_s = 0
        cls.max_delay_s = 0

    challenge = "<html><body>captcha required, please verify</body></html>"
    respx.get(url__regex=r"https?://(www\.)?coupang\.com/.*").mock(
        return_value=httpx.Response(200, text=challenge)
    )
    # Quiet the rest
    for pattern in (
        r"https?://search\.11st\.co\.kr/.*",
        r"https?://browse\.gmarket\.co\.kr/.*",
        r"https?://www\.musinsa\.com/.*",
    ):
        respx.get(url__regex=pattern).mock(
            return_value=httpx.Response(200, text="<html></html>")
        )
    respx.get(NAVER_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"items": []})
    )

    await _drain_sse("/search/stream?q=검정 셔츠")

    with Session(engine) as session:
        rows = session.exec(select(AdapterWarning)).all()
        assert any(r.kind == "bot_detection_suspected" and r.shop_slug == "coupang" for r in rows)


@respx.mock
async def test_phase2_html_zero_results_creates_warning(monkeypatch) -> None:
    _reset_state()
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    for cls in (CoupangAdapter, ElevenstAdapter, GmarketAdapter, MusinsaAdapter):
        cls.min_delay_s = 0
        cls.max_delay_s = 0

    # All 4 HTML sites return empty markup (no cards matched)
    for pattern in (
        r"https?://(www\.)?coupang\.com/.*",
        r"https?://search\.11st\.co\.kr/.*",
        r"https?://browse\.gmarket\.co\.kr/.*",
        r"https?://www\.musinsa\.com/.*",
    ):
        respx.get(url__regex=pattern).mock(
            return_value=httpx.Response(200, text="<html><body>no items</body></html>")
        )
    respx.get(NAVER_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"items": []})
    )

    await _drain_sse("/search/stream?q=검정 셔츠")

    with Session(engine) as session:
        rows = session.exec(select(AdapterWarning)).all()
        zero_warnings = [r for r in rows if r.kind == "zero_results_suspicious"]
        assert len(zero_warnings) >= 1
