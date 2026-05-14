from __future__ import annotations

from typing import Optional

from selectolax.parser import Node

from app.adapters.html_base import HtmlSearchAdapter, _attr, _text, _to_int_price
from app.adapters.types import SearchResult


class MusinsaAdapter(HtmlSearchAdapter):
    """무신사 search results adapter.

    Musinsa is a Next.js SPA whose product cards use generated styled-component
    class names that rotate per build. We anchor on stable analytics
    attributes (data-item-id / data-price / aria-label) instead.
    """

    slug = "musinsa"
    display_name = "무신사"

    search_url_template = "https://www.musinsa.com/search/musinsa/goods?q={keyword}"
    # Each product surfaces two anchors (image + title) with the same product id.
    # We grab the image anchor (it carries the gtm-view-item-list class) and
    # extract every field from its data-* attributes; this dedupes naturally.
    card_selector = "a.gtm-view-item-list[data-item-id][data-price]"
    title_selector = ""
    price_selector = ""
    link_selector = ""
    image_selector = ""
    specs_selector = ""

    def _parse_one_card(self, card: Node) -> Optional[SearchResult]:
        # title: prefer the sibling/parent text (the visible product name);
        # fall back to aria-label minus the generic suffix.
        title = self._musinsa_title(card)
        if not title:
            return None
        href = self._absolutize(_attr(card, "href"))
        if not href:
            return None
        price_raw = _attr(card, "data-price")
        price = _to_int_price(price_raw)
        img_node = card.css_first("img")
        image = self._absolutize(
            _attr(img_node, "src") or _attr(img_node, "data-src")
        )
        brand = _attr(card, "data-item-brand") or _attr(card, "data-item-category")
        return SearchResult(
            shop_slug=self.slug,
            title=title,
            price=price,
            image_url=image or None,
            product_url=href,
            raw_specs=brand,
        )

    def _musinsa_title(self, card: Node) -> str:
        # aria-label often holds a generic "상품 상세로 이동". The real product
        # name lives in another anchor (also gtm-select-item) under the same
        # card container — walk up and pick the first aria-label whose
        # generic-suffix-stripped form is non-empty.
        def clean(label: str) -> str:
            return (
                label.replace("상품상세로 이동", "")
                .replace("상품 상세로 이동", "")
                .strip()
            )

        cleaned = clean(_attr(card, "aria-label"))
        if cleaned:
            return cleaned

        node: Optional[Node] = card.parent
        for _ in range(5):
            if node is None:
                break
            for sib in node.css("a.gtm-select-item[aria-label]"):
                cleaned = clean(_attr(sib, "aria-label"))
                if cleaned:
                    return cleaned
            node = node.parent
        return ""
