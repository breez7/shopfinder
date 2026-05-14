from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters.naver import NAVER_SEARCH_URL, NaverAdapter, _clean_title
from app.adapters.types import ParsedConditions


def test_clean_title_strips_b_tags_and_decodes_entities() -> None:
    assert _clean_title("<b>검정</b> 셔츠 &amp; 바지") == "검정 셔츠 & 바지"


@respx.mock
async def test_returns_results_with_valid_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")

    respx.get(NAVER_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "title": "<b>검정</b> 남방",
                        "link": "https://shopping.naver.com/p/1",
                        "image": "https://img/1.jpg",
                        "lprice": "15000",
                        "brand": "BrandA",
                        "mallName": "MallA",
                    },
                    {
                        "title": "검정 코트",
                        "link": "https://shopping.naver.com/p/2",
                        "image": "https://img/2.jpg",
                        "lprice": "300000",  # over budget
                        "brand": "BrandB",
                        "mallName": "MallB",
                    },
                ]
            },
        )
    )

    adapter = NaverAdapter()
    results = []
    async for r in adapter.search(ParsedConditions(color="검정", max_price=20000)):
        results.append(r)

    # The 300000 row is filtered out by max_price
    assert len(results) == 1
    assert results[0].shop_slug == "naver"
    assert results[0].title == "검정 남방"
    assert results[0].price == 15000
    assert results[0].error is False


async def test_missing_creds_yields_single_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)

    adapter = NaverAdapter()
    results = []
    async for r in adapter.search(ParsedConditions(category="남방")):
        results.append(r)
    assert len(results) == 1
    assert results[0].error is True
    assert "credentials" in (results[0].error_message or "").lower()


@respx.mock
async def test_handles_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    respx.get(NAVER_SEARCH_URL).mock(return_value=httpx.Response(401, json={}))

    adapter = NaverAdapter()
    results = [r async for r in adapter.search(ParsedConditions(category="남방"))]
    assert len(results) == 1
    assert results[0].error is True
    assert "401" in (results[0].error_message or "")


@respx.mock
async def test_handles_429_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    respx.get(NAVER_SEARCH_URL).mock(return_value=httpx.Response(429, json={}))

    adapter = NaverAdapter()
    results = [r async for r in adapter.search(ParsedConditions(category="남방"))]
    assert len(results) == 1
    assert results[0].error is True
    assert "429" in (results[0].error_message or "")


async def test_empty_keyword_yields_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    adapter = NaverAdapter()
    results = [r async for r in adapter.search(ParsedConditions())]
    # No keyword → error before HTTP call
    assert len(results) == 1
    assert results[0].error is True
