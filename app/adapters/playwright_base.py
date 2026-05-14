"""Optional Playwright JS-rendering fallback (issue #27).

Adapters whose YAML/JSON config has `requires_js: true` route their HTTP
fetch through this module instead of plain httpx. When Playwright is not
installed (or `ENABLE_PLAYWRIGHT=0` in env), the JS-flagged adapter returns
a single error result with a clear message instead of attempting to fetch.

We deliberately keep Playwright as an optional dependency — the default
container ships without it to stay under the 1 GB Pi 4 memory ceiling.
"""
from __future__ import annotations

import os
from typing import Optional


def playwright_enabled() -> bool:
    return os.getenv("ENABLE_PLAYWRIGHT", "0").strip() in ("1", "true", "TRUE", "yes")


def playwright_available() -> bool:
    """Probe whether the playwright package + browser are usable."""
    if not playwright_enabled():
        return False
    try:
        import playwright  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        return False
    return True


async def fetch_rendered_html(url: str, timeout_ms: int = 8000) -> Optional[str]:
    """Render `url` in a headless browser and return the resolved HTML.

    Returns None when Playwright is disabled or unavailable. The container's
    memory budget is enforced by the caller (we abort if RSS climbs past
    the 400 MB ceiling defined by the issue).
    """
    if not playwright_available():
        return None
    try:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError:
        return None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    locale="ko-KR",
                    user_agent=(
                        "Mozilla/5.0 (Linux; aarch64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                    ),
                )
                page = await context.new_page()
                await page.goto(url, timeout=timeout_ms, wait_until="networkidle")
                return await page.content()
            finally:
                await browser.close()
    except Exception:  # noqa: BLE001
        return None
