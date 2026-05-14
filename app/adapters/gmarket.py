from __future__ import annotations

from app.adapters.html_base import HtmlSearchAdapter


class GmarketAdapter(HtmlSearchAdapter):
    """G마켓 search results adapter."""

    slug = "gmarket"
    display_name = "G마켓"

    search_url_template = "https://browse.gmarket.co.kr/search?keyword={keyword}"
    card_selector = "div.box__item-container"
    title_selector = ".text__item"
    price_selector = ".text__value"
    link_selector = "a.link__item"
    image_selector = "img.image__lazy"
    specs_selector = ".text__item-sub"
