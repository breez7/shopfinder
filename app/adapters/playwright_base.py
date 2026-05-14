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


DESKTOP_UA = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-G998N) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)


async def fetch_rendered_html(
    url: str,
    timeout_ms: int = 20000,
    wait_until: str = "domcontentloaded",
    wait_for_selector: Optional[str] = None,
    wait_selector_timeout_ms: int = 10000,
    mobile: bool = False,
    user_agent: Optional[str] = None,
) -> Optional[str]:
    """Render `url` in a headless browser and return the resolved HTML.

    Returns None when Playwright is disabled or unavailable. When
    `wait_for_selector` is given, blocks (with its own budget) until that
    selector appears — handy for SPAs that hydrate after the initial DOM.
    `mobile=True` switches to a mobile UA + viewport (useful for sites that
    serve a lighter / less-protected mobile page).
    """
    if not playwright_available():
        return None
    try:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError:
        return None

    if user_agent is None:
        user_agent = MOBILE_UA if mobile else DESKTOP_UA
    viewport = {"width": 390, "height": 844} if mobile else {"width": 1366, "height": 768}

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                context = await browser.new_context(
                    locale="ko-KR",
                    user_agent=user_agent,
                    viewport=viewport,
                    is_mobile=mobile,
                )
                page = await context.new_page()
                await page.goto(url, timeout=timeout_ms, wait_until=wait_until)
                if wait_for_selector:
                    try:
                        await page.wait_for_selector(
                            wait_for_selector, timeout=wait_selector_timeout_ms
                        )
                    except Exception:
                        pass  # selector never appeared — return whatever we have
                # Always try a short networkidle wait so SPA hydration of
                # sibling cards completes before we capture the DOM.
                try:
                    await page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    pass
                return await page.content()
            finally:
                await browser.close()
    except Exception:  # noqa: BLE001
        return None
