"""Issue #23: result cards open original page in new tab + click is logged.

Most of this functionality was implemented during #9 (card markup) and #10
(POST /click + JS handler). These tests pin the contract so a regression
shows up immediately.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.adapters.types import SearchResult

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _render_card(result: SearchResult) -> str:
    return templates.get_template("partials/result_card.html").render(r=result)


def test_result_card_opens_in_new_tab_with_noopener() -> None:
    r = SearchResult(
        shop_slug="naver",
        title="검정 남방",
        price=15000,
        product_url="https://shopping.naver.com/p/1",
        image_url="https://img/1.jpg",
    )
    html = _render_card(r)
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    assert "https://shopping.naver.com/p/1" in html


def test_result_card_renders_minimum_visible_fields() -> None:
    r = SearchResult(
        shop_slug="coupang",
        title="t",
        price=9900,
        product_url="https://x/p",
    )
    html = _render_card(r)
    assert "9,900원" in html
    assert "coupang" in html


def test_result_card_omits_image_when_missing() -> None:
    r = SearchResult(shop_slug="x", title="t", product_url="u")
    html = _render_card(r)
    assert "<img" not in html


def test_result_card_renders_matched_reason_chip_when_present() -> None:
    r = SearchResult(
        shop_slug="x",
        title="t",
        product_url="u",
        match_score=92.0,
        matched_reason="폴리에스테르 82%, 루즈핏 표기",
    )
    html = _render_card(r)
    assert "폴리에스테르 82%" in html
    assert "matched-reason" in html
