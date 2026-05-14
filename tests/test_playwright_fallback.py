from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.adapters.html_base import HtmlSearchAdapter
from app.adapters.playwright_base import playwright_available, playwright_enabled
from app.adapters.types import ParsedConditions, SearchResult


class _JsAdapter(HtmlSearchAdapter):
    slug = "jsadapter"
    search_url_template = "https://example.com/?q={keyword}"
    card_selector = "li"
    title_selector = ".t"
    price_selector = ".p"
    link_selector = "a"
    image_selector = "img"
    requires_js = True


def test_playwright_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_PLAYWRIGHT", raising=False)
    assert playwright_enabled() is False
    assert playwright_available() is False


def test_playwright_enabled_flag_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_PLAYWRIGHT", "1")
    assert playwright_enabled() is True
    # Available only if the package is importable (we don't ship it by default)
    # so the result depends on the test environment.


async def test_js_adapter_returns_error_when_playwright_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_PLAYWRIGHT", raising=False)
    adapter = _JsAdapter()
    adapter.min_delay_s = 0
    adapter.max_delay_s = 0
    results: list[SearchResult] = []
    async for r in adapter.search(ParsedConditions(free_text="x")):
        results.append(r)
    assert len(results) == 1
    assert results[0].error is True
    assert "Playwright" in (results[0].error_message or "")


def test_yaml_adapter_supports_requires_js_config_key() -> None:
    from app.adapters.yaml_adapter import YamlHtmlAdapter

    a = YamlHtmlAdapter(
        config={
            "search_url_template": "https://x/?q={keyword}",
            "card_selector": "li",
            "title_selector": "t",
            "price_selector": "p",
            "link_selector": "a",
            "image_selector": "img",
            "requires_js": True,
        }
    )
    assert a.requires_js is True


def test_yaml_adapter_default_requires_js_false() -> None:
    from app.adapters.yaml_adapter import YamlHtmlAdapter

    a = YamlHtmlAdapter(
        config={
            "search_url_template": "https://x/?q={keyword}",
            "card_selector": "li",
            "title_selector": "t",
            "price_selector": "p",
            "link_selector": "a",
            "image_selector": "img",
        }
    )
    assert a.requires_js is False
