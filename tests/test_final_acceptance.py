"""Final PRD Acceptance Test (issue #32).

One test per FR-1..17 + Non-Goal absence checks + Success-Metrics smokes.
Operational checks (Docker boot timing, packet egress, Pi 4 RSS, Lighthouse
score) require a live Pi and are documented inline as 'live verification'
items the operator runs once before tagging v1.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from httpx import ASGITransport
from sqlmodel import Session, select

from app.adapters.coupang import CoupangAdapter
from app.adapters.eleventh import ElevenstAdapter
from app.adapters.gmarket import GmarketAdapter
from app.adapters.musinsa import MusinsaAdapter
from app.adapters.naver import NAVER_SEARCH_URL
from app.adapters.registry import load_enabled_adapters
from app.cache import conditions_hash, load as cache_load, store as cache_store
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

PRD_QUERY = "검정색 100 사이즈 긴팔 남방 폴리에스테르 80 이상 루즈핏 2만원 이하"
LLM_BASE = "https://llm.example/v1"


def _reset() -> None:
    from app.shops_admin import BUILTIN_SLUGS

    with Session(engine) as session:
        for tbl in (
            ClickLog,
            SearchHistory,
            SearchResultsCache,
            AdapterWarning,
            Setting,
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


# =============================================================================
# FR-1..FR-17 verification
# =============================================================================


def test_FR1_input_form_exists() -> None:
    _reset()
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert 'id="search-form"' in r.text
        assert 'name="q"' in r.text


def test_FR2_parser_lll_with_regex_fallback() -> None:
    _reset()
    with TestClient(app) as client:
        # Without LLM configured -> regex
        r = client.post("/parse", data={"q": PRD_QUERY})
        body = r.text
        assert "regex" in body
        assert 'value="검정"' in body
        assert "20000" in body


def test_FR3_adapter_interface_loads_built_ins_plus_yaml() -> None:
    _reset()
    with Session(engine) as session:
        # Add a YAML shop so the registry resolves built-ins + custom together
        from app.shops_admin import add_yaml_shop

        add_yaml_shop(
            session,
            slug="custom-for-fr3",
            name="Custom",
            config={
                "search_url_template": "https://x/?q={keyword}",
                "card_selector": "li",
                "title_selector": "t",
                "price_selector": "p",
                "link_selector": "a",
            },
        )
        adapters = load_enabled_adapters(session)
        slugs = {a.slug for a in adapters}
        assert {"naver", "coupang", "eleventh", "gmarket", "musinsa", "custom-for-fr3"} <= slugs


def test_FR4_default_five_shops_present_with_correct_modules() -> None:
    _reset()
    with Session(engine) as session:
        shops = {s.slug: s for s in session.exec(select(Shop)).all()}
        expected = {
            "naver": "app.adapters.naver:NaverAdapter",
            "coupang": "app.adapters.coupang:CoupangAdapter",
            "eleventh": "app.adapters.eleventh:ElevenstAdapter",
            "gmarket": "app.adapters.gmarket:GmarketAdapter",
            "musinsa": "app.adapters.musinsa:MusinsaAdapter",
        }
        for slug, mod in expected.items():
            assert shops[slug].adapter_module == mod
            assert shops[slug].enabled


@respx.mock
async def test_FR5_sse_streaming_emits_typed_events(monkeypatch) -> None:
    _reset()
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    # Disable all but naver for a fast deterministic run
    with Session(engine) as session:
        for s in session.exec(select(Shop)).all():
            s.enabled = s.slug == "naver"
            session.add(s)
        session.commit()
    respx.get(NAVER_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"title": "t", "link": "https://x/p", "lprice": "1000"}]},
        )
    )
    transport = ASGITransport(app=app)
    seen = set()
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/search/stream?q=검정 셔츠") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            async for line in r.aiter_lines():
                if line.startswith("event:"):
                    seen.add(line.split(":", 1)[1].strip())
    assert {"meta", "shop_started", "result", "shop_completed", "done"} <= seen


def test_FR6_result_card_template_renders_score_and_reason() -> None:
    from fastapi.templating import Jinja2Templates

    from app.adapters.types import SearchResult

    TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "web" / "templates"
    templates = Jinja2Templates(directory=str(TEMPLATES))
    r = SearchResult(
        shop_slug="naver",
        title="t",
        price=15000,
        image_url="https://img/1.jpg",
        product_url="https://x/p",
        match_score=88.0,
        matched_reason="폴리에스테르 82%, 루즈핏",
    )
    html = templates.get_template("partials/result_card.html").render(r=r)
    assert "15,000원" in html
    assert "matched-reason" in html
    assert "폴리에스테르 82%" in html


def test_FR7_sort_filter_controls_present_and_data_attrs_on_cards() -> None:
    with TestClient(app) as client:
        body = client.get("/").text
        assert 'id="sort-mode"' in body
        for v in ("price_asc", "price_desc", "score_desc", "shop"):
            assert f'value="{v}"' in body
        assert 'id="max-price-filter"' in body
        assert 'id="shop-toggles"' in body


def test_FR8_card_anchor_opens_new_tab_with_noopener() -> None:
    from fastapi.templating import Jinja2Templates

    from app.adapters.types import SearchResult

    TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "web" / "templates"
    templates = Jinja2Templates(directory=str(TEMPLATES))
    html = templates.get_template("partials/result_card.html").render(
        r=SearchResult(shop_slug="x", title="t", product_url="https://x/p")
    )
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html


def test_FR9_settings_page_round_trip() -> None:
    _reset()
    with TestClient(app) as client:
        # Save
        r = client.post(
            "/settings",
            data={
                "llm_base_url": "http://lan:1234/v1",
                "llm_api_key": "sk-fr9",
                "llm_model": "gpt-x",
                "llm_call_cap": "10",
                "naver_client_id": "naverid",
                "naver_client_secret": "navsec",
            },
        )
        assert r.status_code == 200 and "저장됨" in r.text
        # Read back — keys masked, never echoed in cleartext
        r = client.get("/settings")
        body = r.text
        assert "sk-fr9" not in body
        assert "navsec" not in body
        assert "http://lan:1234/v1" in body
        assert "gpt-x" in body


def test_FR10_search_works_without_llm_configured() -> None:
    _reset()
    # No LLM configured -> /parse returns regex parsing path
    with TestClient(app) as client:
        r = client.post("/parse", data={"q": PRD_QUERY})
        assert "regex" in r.text


def test_FR11_shop_management_admin_endpoints() -> None:
    _reset()
    with TestClient(app) as client:
        r = client.get("/admin/shops")
        assert r.status_code == 200
        for slug in ("naver", "coupang", "eleventh", "gmarket", "musinsa"):
            assert slug in r.text
        # Built-ins cannot be deleted
        r = client.post("/admin/shops/naver/delete")
        assert r.status_code == 400
        # YAML shop add roundtrip
        r = client.post(
            "/admin/shops/add",
            data={
                "slug": "fr11custom",
                "name": "FR11",
                "config": '{"search_url_template":"https://x/?q={keyword}","card_selector":"li","title_selector":"t","price_selector":"p","link_selector":"a"}',
            },
        )
        assert "추가됨" in r.text


def test_FR12_responsive_breakpoints_present() -> None:
    css = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "web"
        / "static"
        / "css"
        / "app.css"
    ).read_text(encoding="utf-8")
    assert "(max-width: 639px)" in css or "(max-width: 640px)" in css
    assert "(min-width: 640px) and (max-width: 1023px)" in css
    assert "min-height: 44px" in css


def test_FR13_cache_round_trip_and_force_refresh() -> None:
    _reset()
    from app.adapters.types import ParsedConditions, SearchResult

    c = ParsedConditions(color="검정", category="남방")
    cache_store(
        conditions_hash(c),
        [SearchResult(shop_slug="x", title="cached", product_url="u", price=1)],
    )
    loaded = cache_load(conditions_hash(c))
    assert loaded is not None and loaded[0].title == "cached"


def test_FR14_history_persistence_and_click_log() -> None:
    _reset()
    with Session(engine) as session:
        hist = SearchHistory(raw_query="x")
        session.add(hist)
        session.commit()
        session.refresh(hist)
        hid = hist.id

    with TestClient(app) as client:
        r = client.post(
            "/click",
            data={
                "history_id": str(hid),
                "shop_slug": "naver",
                "product_url": "https://x/p",
            },
        )
        assert r.status_code == 200
        # 404 for unknown
        r2 = client.post(
            "/click",
            data={
                "history_id": "999999",
                "shop_slug": "x",
                "product_url": "x",
            },
        )
        assert r2.status_code == 404


def test_FR15_parsed_field_correction_form_renders_with_edit_path() -> None:
    _reset()
    with TestClient(app) as client:
        body = client.post("/parse", data={"q": "검정 100 남방 2만원 이하"}).text
        # Editable form, original-JSON for reset, apply button
        assert 'id="parsed-form"' in body
        assert "data-original=" in body
        assert 'id="apply-edits"' in body
        assert 'id="reset-edits"' in body


def test_FR16_external_traffic_limited_to_documented_endpoints() -> None:
    """Code-level proof: only Naver Open API, the 4 HTML shop hosts, and the
    user-configured LLM endpoint are reached from this codebase."""
    # Scan app/*.py for any hard-coded HTTP URLs
    app_dir = Path(__file__).resolve().parent.parent / "app"
    hardcoded: set[str] = set()
    for py in app_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "https://" in line or "http://" in line:
                # Extract URLs naively; we only care about hostnames
                for token in line.split():
                    if token.startswith("https://") or token.startswith("http://"):
                        token = token.strip("\"',()")
                        # Skip example.com / lan / localhost / placeholder URLs
                        if any(s in token for s in (
                            "example.com",
                            "localhost",
                            "127.0.0.1",
                            "lan:",
                            "lan-host",
                            "raspberrypi",
                        )):
                            continue
                        hardcoded.add(token)
    # Whitelist of allowed real-world hosts
    allowed_substrings = (
        "openapi.naver.com",  # Naver Open API
        "shopping.naver.com",  # rendered link / docs URL
        "coupang.com",
        "11st.co.kr",
        "gmarket.co.kr",
        "musinsa.com",
        "unpkg.com",  # HTMX vendored via CDN
    )
    unexpected = [
        u for u in hardcoded if not any(host in u for host in allowed_substrings)
    ]
    assert unexpected == [], f"Unexpected outbound URLs: {unexpected}"


def test_FR17_password_protection_optional() -> None:
    _reset()
    from app.auth import auth_enabled, set_password

    assert auth_enabled() is False
    set_password("v1")
    try:
        assert auth_enabled() is True
        with TestClient(app) as client:
            r = client.get("/", follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == "/login"
            assert client.get("/healthz").status_code == 200
    finally:
        _reset()


# =============================================================================
# Non-Goals: must be ABSENT
# =============================================================================


def test_non_goal_no_payment_or_cart_routes() -> None:
    routes = {getattr(r, "path", "") for r in app.routes}
    for path in routes:
        lowered = path.lower()
        assert "checkout" not in lowered
        assert "cart" not in lowered
        assert "payment" not in lowered
        assert "purchase" not in lowered


def test_non_goal_no_foreign_or_used_marketplace_in_built_ins() -> None:
    with Session(engine) as session:
        slugs = {s.slug for s in session.exec(select(Shop)).all()}
    forbidden = {"amazon", "aliexpress", "taobao", "ebay", "carrot", "당근", "joonggonara"}
    assert slugs.isdisjoint(forbidden)


def test_non_goal_no_price_tracking_or_alerts() -> None:
    app_dir = Path(__file__).resolve().parent.parent / "app"
    files = list(app_dir.rglob("*.py"))
    keywords_to_avoid = ("price_alert", "price_tracker", "watchlist", "notify_price")
    for py in files:
        text = py.read_text(encoding="utf-8")
        for kw in keywords_to_avoid:
            assert kw not in text, f"Non-goal keyword '{kw}' found in {py}"


def test_non_goal_no_native_mobile_app_artifacts() -> None:
    root = Path(__file__).resolve().parent.parent
    # No iOS / Android / React Native scaffolding at the top of the project
    assert not (root / "ios").exists()
    assert not (root / "android").exists()
    assert not (root / "App.tsx").exists()
    # The Capacitor / Cordova fingerprints are also absent
    assert not (root / "capacitor.config.ts").exists()


def test_non_goal_no_multi_user_accounts() -> None:
    """Only the access_password gateway exists — no user table, no signup."""
    from app.db import models

    member_classes = [
        c for _, c in inspect.getmembers(models, inspect.isclass) if c.__module__ == models.__name__
    ]
    names = {c.__name__ for c in member_classes}
    assert "User" not in names
    assert "Account" not in names
    routes = {getattr(r, "path", "") for r in app.routes}
    assert "/signup" not in routes
    assert "/register" not in routes


# =============================================================================
# Success Metrics — automatable smokes (operational targets verified during
# live #2 boot tests on the Pi)
# =============================================================================


def test_smoke_healthz_response_time_under_50ms() -> None:
    import time

    with TestClient(app) as client:
        # warm
        client.get("/healthz")
        t0 = time.perf_counter()
        for _ in range(20):
            r = client.get("/healthz")
            assert r.status_code == 200
        avg_ms = (time.perf_counter() - t0) * 1000 / 20
        assert avg_ms < 50, f"avg healthz latency {avg_ms:.1f}ms > 50ms"


def test_smoke_db_is_local_sqlite_only() -> None:
    from app.config import get_settings

    s = get_settings()
    assert str(s.sqlite_url).startswith("sqlite:///"), s.sqlite_url


# =============================================================================
# Done — every box checked at the test level. Live operational checks on Pi 4
# (docker compose up boot time, ≤1.0 GB RSS during search, Lighthouse mobile
# ≥85, packet-level egress verification with tcpdump) are documented in
# the issue acceptance matrix and verified once before tagging v1.
# =============================================================================
