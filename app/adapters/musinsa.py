from __future__ import annotations

from app.adapters.html_base import HtmlSearchAdapter


class MusinsaAdapter(HtmlSearchAdapter):
    """무신사 search results adapter."""

    slug = "musinsa"
    display_name = "무신사"

    search_url_template = "https://www.musinsa.com/search/musinsa/goods?q={keyword}"
    card_selector = "li.li_box"
    title_selector = ".item_title"
    price_selector = ".price"
    link_selector = ".list_info a"
    image_selector = "img"
    specs_selector = ".article_info_brand"
