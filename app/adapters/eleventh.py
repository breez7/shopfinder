from __future__ import annotations

from app.adapters.html_base import HtmlSearchAdapter


class ElevenstAdapter(HtmlSearchAdapter):
    """11번가 search results adapter."""

    slug = "eleventh"
    display_name = "11번가"

    search_url_template = "https://search.11st.co.kr/Search.tmall?kwd={keyword}"
    card_selector = "div.c-card-item"
    title_selector = ".c-card-item__name"
    price_selector = ".c-card-item__price"
    link_selector = "a.c-card-item__anchor"
    image_selector = ".c-card-item__thumb img"
    specs_selector = ".c-card-item__delivery"
