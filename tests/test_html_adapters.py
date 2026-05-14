from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from sqlmodel import Session, select

from app.adapters.coupang import CoupangAdapter
from app.adapters.eleventh import ElevenstAdapter
from app.adapters.gmarket import GmarketAdapter
from app.adapters.html_base import HtmlSearchAdapter, _to_int_price
from app.adapters.musinsa import MusinsaAdapter
from app.adapters.types import ParsedConditions
from app.db.models import AdapterWarning
from app.db.session import engine

FIXTURES = Path(__file__).parent / "fixtures"


def _truncate_warnings() -> None:
    with Session(engine) as session:
        for w in session.exec(select(AdapterWarning)).all():
            session.delete(w)
        session.commit()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("15,900원", 15900),
        ("15900", 15900),
        ("\n  20,000  원", 20000),
        ("", None),
        ("free shipping", None),
    ],
)
def test_to_int_price(raw: str, expected: int | None) -> None:
    assert _to_int_price(raw) == expected


@respx.mock
async def test_coupang_adapter_parses_fixture() -> None:
    _truncate_warnings()
    html = (FIXTURES / "coupang_sample.html").read_text(encoding="utf-8")
    respx.get(url__regex=r"https://www\.coupang\.com/np/search.*").mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )

    adapter = CoupangAdapter()
    # No delay during tests
    adapter.min_delay_s = 0
    adapter.max_delay_s = 0
    results = [
        r
        async for r in adapter.search(
            ParsedConditions(color="검정", category="남방", max_price=20000)
        )
    ]
    # max_price filters the 250000 row
    assert len(results) == 1
    r = results[0]
    assert r.shop_slug == "coupang"
    assert r.price == 15900
    assert r.image_url == "https://thumb/coupang/123.jpg"
    assert r.product_url.endswith("/vp/products/123")
    assert "남방" in r.title


@respx.mock
async def test_html_adapter_bot_detection() -> None:
    _truncate_warnings()
    challenge = "<html><body>captcha required, please verify</body></html>"

    class _Test(HtmlSearchAdapter):
        slug = "test"
        search_url_template = "https://example.com/?q={keyword}"
        card_selector = "li"
        title_selector = ".t"
        price_selector = ".p"
        link_selector = "a"
        image_selector = "img"

    _Test.min_delay_s = 0
    _Test.max_delay_s = 0
    respx.get(url__regex=r"https://example\.com.*").mock(
        return_value=httpx.Response(200, text=challenge)
    )

    adapter = _Test()
    results = [r async for r in adapter.search(ParsedConditions(free_text="x"))]
    assert results[0].error is True
    assert "bot" in (results[0].error_message or "")

    with Session(engine) as session:
        rows = session.exec(select(AdapterWarning)).all()
        assert any(r.kind == "bot_detection_suspected" for r in rows)


@respx.mock
async def test_html_adapter_http_500_records_warning() -> None:
    _truncate_warnings()

    class _Test(HtmlSearchAdapter):
        slug = "test500"
        search_url_template = "https://example.com/?q={keyword}"
        card_selector = "li"
        title_selector = ".t"
        price_selector = ".p"
        link_selector = "a"
        image_selector = "img"

    _Test.min_delay_s = 0
    _Test.max_delay_s = 0
    respx.get(url__regex=r"https://example\.com.*").mock(return_value=httpx.Response(500))

    adapter = _Test()
    results = [r async for r in adapter.search(ParsedConditions(free_text="x"))]
    assert results[0].error is True

    with Session(engine) as session:
        rows = session.exec(select(AdapterWarning)).all()
        assert any(r.kind == "http_error" for r in rows)


@respx.mock
async def test_html_adapter_zero_results_logged() -> None:
    _truncate_warnings()

    class _Test(HtmlSearchAdapter):
        slug = "testempty"
        search_url_template = "https://example.com/?q={keyword}"
        card_selector = "li.does-not-exist"
        title_selector = ".t"
        price_selector = ".p"
        link_selector = "a"
        image_selector = "img"

    _Test.min_delay_s = 0
    _Test.max_delay_s = 0
    respx.get(url__regex=r"https://example\.com.*").mock(
        return_value=httpx.Response(200, text="<html><body>nothing here</body></html>")
    )

    adapter = _Test()
    results = [r async for r in adapter.search(ParsedConditions(free_text="x"))]
    assert results == []

    with Session(engine) as session:
        rows = session.exec(select(AdapterWarning)).all()
        assert any(r.kind == "zero_results_suspicious" for r in rows)


@pytest.mark.parametrize(
    "cls,slug,url_substring",
    [
        (CoupangAdapter, "coupang", "coupang.com/np/search"),
        (ElevenstAdapter, "eleventh", "search.11st.co.kr"),
        (GmarketAdapter, "gmarket", "m.gmarket.co.kr"),
        (MusinsaAdapter, "musinsa", "musinsa.com/search"),
    ],
)
def test_each_adapter_has_required_config(cls, slug: str, url_substring: str) -> None:
    a = cls()
    assert a.slug == slug
    assert url_substring in a.search_url_template
    assert a.card_selector
    # Musinsa parses every field from data-* attributes on the card anchor
    # itself, so title/price/link selectors are intentionally empty.
    if slug != "musinsa":
        assert a.title_selector
        assert a.price_selector
        assert a.link_selector


@respx.mock
async def test_each_adapter_yields_zero_when_response_has_no_matching_cards() -> None:
    """All four shop adapters must complete without crashing on an unfamiliar HTML."""
    _truncate_warnings()
    for cls in (CoupangAdapter, ElevenstAdapter, GmarketAdapter, MusinsaAdapter):
        respx.reset()
        respx.get(url__regex=r"https?://.*").mock(
            return_value=httpx.Response(200, text="<html></html>")
        )
        a = cls()
        a.min_delay_s = 0
        a.max_delay_s = 0
        results = [r async for r in a.search(ParsedConditions(free_text="아무거나"))]
        assert results == []
