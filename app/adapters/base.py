from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.adapters.types import ParsedConditions, SearchResult


class ShopAdapter(ABC):
    """Contract every shop integration implements.

    `search()` is an async iterator so results can be streamed to the UI as they parse,
    rather than waiting for the slowest adapter.
    """

    slug: str = ""
    display_name: str = ""

    # Per-shop concurrency lock (PRD §6 / §9 anti-blocking policy: 1 in-flight per shop).
    _lock: asyncio.Lock | None = None

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}
        if self._lock is None:
            type(self)._lock = asyncio.Lock()

    @abstractmethod
    async def search(
        self,
        conditions: ParsedConditions,
        max_results: int = 30,
    ) -> AsyncIterator[SearchResult]:
        """Yield SearchResult items as they are parsed.

        Must NOT raise — recoverable errors should be yielded as `SearchResult.make_error()`
        so the orchestrator surfaces them without killing the whole search.
        """
        raise NotImplementedError
        yield  # pragma: no cover  (makes type checker happy: this is an async generator)

    def healthcheck(self) -> bool:
        """Optional override: quick check that creds/config are usable."""
        return True


async def safe_iterate(
    adapter: ShopAdapter,
    conditions: ParsedConditions,
    max_results: int = 30,
) -> AsyncIterator[SearchResult]:
    """Wrap an adapter's search() so any raised exception becomes a single error result
    instead of killing the orchestrator."""
    try:
        async for item in adapter.search(conditions, max_results=max_results):
            yield item
    except Exception as exc:  # noqa: BLE001
        yield SearchResult.make_error(
            shop_slug=adapter.slug,
            message=f"{type(exc).__name__}: {exc}",
        )
