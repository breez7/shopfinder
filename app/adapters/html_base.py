"""Generic HTML-parsing adapter base.

Subclasses declare a search URL template and CSS selectors; the base handles
HTTP fetch (httpx + realistic User-Agent + random jitter), parsing
(selectolax), max_price filtering, warning logging, and bot-detection
heuristics.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
from collections.abc import AsyncIterator
from typing import Optional
from urllib.parse import quote_plus

import httpx
from selectolax.parser import HTMLParser, Node

from app.adapters.base import ShopAdapter
from app.adapters.playwright_base import (
    fetch_rendered_html,
    playwright_available,
    playwright_enabled,
)
from app.adapters.types import ParsedConditions, SearchResult
from app.warnings import (
    KIND_BOT_DETECTION,
    KIND_HTTP_ERROR,
    KIND_PARSE_EXCEPTION,
    KIND_ZERO_RESULTS,
    record_warning,
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT_S = 8.0
_PERCENT_RE = re.compile(r"\d+\s*%")
_NUMBER_RE = re.compile(r"[\d,]+")
_MIN_PRICE_KRW = 100  # below this we're almost certainly looking at a
                     # discount percentage / review count, not a price


def _to_int_price(raw: Optional[str]) -> Optional[int]:
    """Pull a KRW price out of a string that may also carry discount %,
    review counts, star ratings, etc. Strategy:

    1. Strip out `\\d+%` runs (discount percentages — the issue users hit
       where '10%' was being read as a 10 KRW price).
    2. Pick the largest remaining number (price > discount-count > rating
       in basically every real-world card layout).
    3. Reject anything under :data:`_MIN_PRICE_KRW` (100 KRW)."""
    if not raw:
        return None
    cleaned = _PERCENT_RE.sub("", raw)
    best: Optional[int] = None
    for match in _NUMBER_RE.findall(cleaned):
        try:
            val = int(match.replace(",", ""))
        except ValueError:
            continue
        if val < _MIN_PRICE_KRW:
            continue
        if best is None or val > best:
            best = val
    return best


def _text(node: Optional[Node]) -> str:
    return (node.text(strip=True) if node is not None else "") or ""


def _attr(node: Optional[Node], name: str) -> str:
    if node is None:
        return ""
    val = node.attributes.get(name)
    return val or ""


def _force_js_slugs() -> set[str]:
    """Adapter slugs that should be routed through Playwright regardless of
    their class-level `requires_js` flag. Read from the FORCE_JS_ADAPTERS env
    var as a comma-separated list (e.g. "coupang,gmarket,eleventh,musinsa")."""
    raw = os.getenv("FORCE_JS_ADAPTERS", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


class HtmlSearchAdapter(ShopAdapter):
    """Subclasses override the class-level fields below."""

    search_url_template: str = ""  # e.g. "https://example.com/search?q={keyword}"
    card_selector: str = ""
    title_selector: str = ""
    price_selector: str = ""
    link_selector: str = ""
    image_selector: str = ""
    specs_selector: str = ""
    requires_js: bool = False  # set true for sites that need Playwright rendering
    mobile_emulation: bool = False  # mobile UA + viewport when Playwright fetches

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config=config)
        # Operator can force-enable Playwright for specific built-in adapters
        # without touching their Python class.
        if self.slug and self.slug in _force_js_slugs():
            self.requires_js = True

    # If a response body contains any of these substrings we assume bot detection
    bot_signatures: tuple[str, ...] = (
        "captcha",
        "Are you a human",
        "비정상적인 접근",
        "차단되었습니다",
    )

    min_delay_s: float = 0.5
    max_delay_s: float = 1.5

    def _build_url(self, conditions: ParsedConditions) -> str:
        keyword = conditions.keyword().strip() or "상품"
        return self.search_url_template.format(keyword=quote_plus(keyword))

    def _absolutize(self, href: str) -> str:
        if not href:
            return ""
        if href.startswith("//"):
            return "https:" + href
        return href

    async def _fetch(self, url: str) -> Optional[httpx.Response]:
        await asyncio.sleep(random.uniform(self.min_delay_s, self.max_delay_s))
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml",
        }
        async with self._lock:
            try:
                async with httpx.AsyncClient(
                    timeout=DEFAULT_TIMEOUT_S, follow_redirects=True
                ) as client:
                    return await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                record_warning(self.slug, KIND_HTTP_ERROR, f"{type(exc).__name__}: {exc}")
                return None

    async def _fetch_body(self, url: str) -> tuple[Optional[str], Optional[int]]:
        """Return (html, status_code). For requires_js adapters routes through
        Playwright (issue #27) when enabled; otherwise short-circuits with a
        clear status_code=0 + None body that callers treat as 'unavailable'."""
        if self.requires_js:
            if not playwright_enabled():
                return None, 0  # caller emits the 'enable Playwright' error
            if not playwright_available():
                record_warning(
                    self.slug,
                    KIND_HTTP_ERROR,
                    "Playwright enabled but not installed",
                )
                return None, 0
            await asyncio.sleep(random.uniform(self.min_delay_s, self.max_delay_s))
            async with self._lock:
                html = await fetch_rendered_html(
                    url,
                    wait_for_selector=self.card_selector or None,
                    mobile=self.mobile_emulation,
                )
            if html is None:
                record_warning(
                    self.slug, KIND_HTTP_ERROR, "Playwright fetch failed"
                )
                return None, 0
            return html, 200

        response = await self._fetch(url)
        if response is None:
            return None, None
        return response.text, response.status_code

    def _is_bot_challenge(self, body: str) -> bool:
        lowered = body[:2000].lower()
        for sig in self.bot_signatures:
            if sig.lower() in lowered:
                return True
        return False

    def _parse_one_card(self, card: Node) -> Optional[SearchResult]:
        try:
            title_node = card.css_first(self.title_selector) if self.title_selector else None
            price_node = card.css_first(self.price_selector) if self.price_selector else None
            link_node = card.css_first(self.link_selector) if self.link_selector else None
            image_node = card.css_first(self.image_selector) if self.image_selector else None
            specs_node = card.css_first(self.specs_selector) if self.specs_selector else None
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"selector raise: {exc}") from exc

        title = _text(title_node) or _attr(title_node, "title")
        price = _to_int_price(_text(price_node))
        url = self._absolutize(_attr(link_node, "href"))
        image = self._absolutize(_attr(image_node, "src") or _attr(image_node, "data-src"))
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

    async def search(
        self,
        conditions: ParsedConditions,
        max_results: int = 30,
    ) -> AsyncIterator[SearchResult]:
        if not self.search_url_template or not self.card_selector:
            yield SearchResult.make_error(self.slug, "adapter not configured")
            return

        url = self._build_url(conditions)
        body, status = await self._fetch_body(url)
        if body is None:
            if self.requires_js and not playwright_enabled():
                yield SearchResult.make_error(
                    self.slug,
                    "enable Playwright (ENABLE_PLAYWRIGHT=1) to use this adapter",
                )
                return
            yield SearchResult.make_error(self.slug, "HTTP error (see warnings)")
            return
        if status not in (None, 200):
            msg = f"HTTP {status}"
            record_warning(self.slug, KIND_HTTP_ERROR, msg)
            yield SearchResult.make_error(self.slug, msg)
            return
        if self._is_bot_challenge(body):
            record_warning(
                self.slug,
                KIND_BOT_DETECTION,
                "challenge page detected",
                snippet=body[:300],
            )
            yield SearchResult.make_error(self.slug, "bot challenge page")
            return

        try:
            tree = HTMLParser(body)
            cards = tree.css(self.card_selector)
        except Exception as exc:  # noqa: BLE001
            record_warning(self.slug, KIND_PARSE_EXCEPTION, str(exc))
            yield SearchResult.make_error(self.slug, f"parse error: {exc}")
            return

        if not cards:
            record_warning(
                self.slug,
                KIND_ZERO_RESULTS,
                f"no cards matched selector {self.card_selector!r}",
            )

        yielded = 0
        max_price = conditions.max_price
        for card in cards:
            if yielded >= max_results:
                break
            try:
                result = self._parse_one_card(card)
            except Exception as exc:  # noqa: BLE001
                record_warning(self.slug, KIND_PARSE_EXCEPTION, str(exc))
                continue
            if result is None:
                continue
            if max_price is not None and result.price is not None and result.price > max_price:
                continue
            yielded += 1
            yield result
