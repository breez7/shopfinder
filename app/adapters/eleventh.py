from __future__ import annotations

from app.adapters.html_base import HtmlSearchAdapter


class ElevenstAdapter(HtmlSearchAdapter):
    """11번가 search results adapter."""

    slug = "eleventh"
    display_name = "11번가"

    search_url_template = "https://search.11st.co.kr/Search.tmall?kwd={keyword}"
    card_selector = "li.c_listing"
    title_selector = ".c_prd_name"
    price_selector = ".value"
    link_selector = "a"
    image_selector = "img"
    specs_selector = ".c_seller"
