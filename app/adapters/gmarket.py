from __future__ import annotations

from typing import Optional

from selectolax.parser import Node

from app.adapters.html_base import HtmlSearchAdapter, _attr, _text, _to_int_price
from app.adapters.types import SearchResult


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
    title_selector = ".text__title"
    price_selector = ".box__price-text"  # final price element (see _parse_one_card)
    link_selector = "a.link__itemcard"
    image_selector = "img.image__itemcard"
    specs_selector = ".box__brand"

    def _parse_one_card(self, card: Node) -> Optional[SearchResult]:
        # Gmarket shows two `.box__price-text` elements on a discounted card
        # (정가 first, 할인가 second). On non-discounted cards there is just
        # one. The actual sale price is always the LAST occurrence.
        price_nodes = card.css(".box__price-text")
        price_text = _text(price_nodes[-1]) if price_nodes else ""
        price = _to_int_price(price_text)

        title_node = card.css_first(self.title_selector) if self.title_selector else None
        title = _text(title_node) or _attr(title_node, "title")

        link_node = card.css_first(self.link_selector) if self.link_selector else None
        url = self._absolutize(_attr(link_node, "href"))

        image_node = card.css_first(self.image_selector) if self.image_selector else None
        image = self._absolutize(
            _attr(image_node, "src") or _attr(image_node, "data-src")
        )

        specs_node = (
            card.css_first(self.specs_selector) if self.specs_selector else None
        )
        specs = _text(specs_node)

        if not title or not url:
            return None
        return SearchResult(
            shop_slug=self.slug,
            title=title,
            price=price,
            image_url=image or None,
            product_url=url,
            raw_specs=specs,
        )
