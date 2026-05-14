from __future__ import annotations

from app.adapters.html_base import HtmlSearchAdapter


class CoupangAdapter(HtmlSearchAdapter):
    """Coupang search results adapter."""

    slug = "coupang"
    display_name = "쿠팡"

    search_url_template = "https://www.coupang.com/np/search?q={keyword}&channel=user"
    card_selector = "li.search-product"
    title_selector = ".name"
    price_selector = ".price-value"
    link_selector = "a.search-product-link"
    image_selector = ".search-product-wrap-img"
    specs_selector = ".rating-total-count"
