"""Issue #22: sort/filter controls visible and result cards carry sort data.

Re-sort is client-side JS so we can't easily exercise the actual reordering
behavior in pytest. These tests verify the markup contract that the JS
relies on: data-price/data-match-score on cards, sort dropdown values, and
filter inputs on the page.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from app.adapters.types import SearchResult
from app.main import app

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def test_index_contains_sort_dropdown_with_four_modes() -> None:
    with TestClient(app) as client:
        r = client.get("/")
        body = r.text
        assert 'id="sort-mode"' in body
        for value in ("price_asc", "price_desc", "score_desc", "shop"):
            assert f'value="{value}"' in body


def test_index_contains_max_price_filter() -> None:
    with TestClient(app) as client:
        r = client.get("/")
        assert 'id="max-price-filter"' in r.text
        assert 'type="number"' in r.text


def test_index_contains_shop_toggles_container() -> None:
    with TestClient(app) as client:
        r = client.get("/")
        assert 'id="shop-toggles"' in r.text


def test_result_card_carries_price_and_match_score_data() -> None:
    r = SearchResult(
        shop_slug="naver",
        title="t",
        price=15000,
        match_score=92.0,
        product_url="https://x/p",
    )
    html = templates.get_template("partials/result_card.html").render(r=r)
    assert 'data-price="15000"' in html
    assert 'data-match-score="92.0"' in html
    assert 'data-shop="naver"' in html


def test_result_card_empty_dataset_when_price_unknown() -> None:
    r = SearchResult(shop_slug="x", title="t", product_url="u")
    html = templates.get_template("partials/result_card.html").render(r=r)
    assert 'data-price=""' in html
    assert 'data-match-score=""' in html
