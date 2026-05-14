from __future__ import annotations

from app.adapters.html_base import HtmlSearchAdapter


class GmarketAdapter(HtmlSearchAdapter):
    """G마켓 search results adapter.

    The desktop site (www.gmarket.co.kr) sits behind a Cloudflare JS challenge
    that no amount of stealth-patching unblocks from headless Chromium on the
    Pi. The mobile site (m.gmarket.co.kr) serves the same product data
    without the challenge, so we use it instead.
    """

    slug = "gmarket"
    display_name = "G마켓"

    search_url_template = "https://m.gmarket.co.kr/n/search?keyword={keyword}"
    mobile_emulation = True

    # Mobile-site DOM (`.box__itemcard` is the product container)
    card_selector = "div.box__itemcard"
    title_selector = ".box__itemcard-title-area"
    price_selector = ".box__price-sale"
    link_selector = "a.link__itemcard"
    image_selector = "img.image__itemcard"
    specs_selector = ".box__brand"
