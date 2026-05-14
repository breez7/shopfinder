from __future__ import annotations

import html
import os
import re
from collections.abc import AsyncIterator
from typing import Optional

import httpx
from sqlmodel import Session

from app.adapters.base import ShopAdapter
from app.adapters.types import ParsedConditions, SearchResult
from app.db.models import Setting
from app.db.session import engine

NAVER_SEARCH_URL = "https://openapi.naver.com/v1/search/shop.json"
TIMEOUT_SECONDS = 8.0
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_title(raw: str) -> str:
    """Strip the <b>...</b> highlight tags Naver returns and decode HTML entities."""
    return html.unescape(_HTML_TAG_RE.sub("", raw or "")).strip()


def _read_settings_credentials() -> tuple[Optional[str], Optional[str]]:
    """Settings-table values win over env so the /settings UI (#15) can override at runtime."""
    try:
        with Session(engine) as session:
            cid = session.get(Setting, "naver_client_id")
            csec = session.get(Setting, "naver_client_secret")
            cid_val = cid.value if cid and cid.value else None
            csec_val = csec.value if csec and csec.value else None
            if cid_val and csec_val:
                return cid_val, csec_val
    except Exception:  # noqa: BLE001 — DB may not exist in some test setups
        pass
    return os.getenv("NAVER_CLIENT_ID"), os.getenv("NAVER_CLIENT_SECRET")


class NaverAdapter(ShopAdapter):
    slug = "naver"
    display_name = "네이버 쇼핑"

    async def search(
        self,
        conditions: ParsedConditions,
        max_results: int = 30,
    ) -> AsyncIterator[SearchResult]:
        client_id, client_secret = _read_settings_credentials()
        if not client_id or not client_secret:
            yield SearchResult.make_error(
                shop_slug=self.slug,
                message="Naver API credentials missing — set NAVER_CLIENT_ID/SECRET or use /settings",
            )
            return

        keyword = conditions.keyword().strip()
        if not keyword:
            yield SearchResult.make_error(self.slug, "Empty keyword after parsing")
            return

        display = max(1, min(max_results, 100))
        params = {"query": keyword, "display": display, "sort": "sim"}
        headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        }

        async with self._lock:
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    response = await client.get(NAVER_SEARCH_URL, params=params, headers=headers)
            except httpx.HTTPError as exc:
                yield SearchResult.make_error(self.slug, f"HTTP error: {exc}")
                return

            if response.status_code == 401:
                yield SearchResult.make_error(self.slug, "Naver auth failed (401)")
                return
            if response.status_code == 429:
                yield SearchResult.make_error(self.slug, "Naver rate limited (429)")
                return
            if response.status_code >= 400:
                yield SearchResult.make_error(
                    self.slug,
                    f"Naver HTTP {response.status_code}: {response.text[:120]}",
                )
                return

            try:
                payload = response.json()
            except ValueError:
                yield SearchResult.make_error(self.slug, "Naver returned non-JSON body")
                return

            items = payload.get("items") or []
            max_price = conditions.max_price
            for item in items:
                try:
                    price = int(item.get("lprice") or 0) or None
                except (TypeError, ValueError):
                    price = None

                if max_price is not None and price is not None and price > max_price:
                    continue

                yield SearchResult(
                    shop_slug=self.slug,
                    title=_clean_title(item.get("title", "")),
                    price=price,
                    image_url=item.get("image"),
                    product_url=item.get("link", ""),
                    raw_specs=" / ".join(
                        filter(None, [item.get("brand"), item.get("mallName"), item.get("category4")])
                    ),
                )
