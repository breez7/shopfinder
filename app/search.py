from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.adapters.base import ShopAdapter, safe_iterate
from app.adapters.types import AdapterEvent, ParsedConditions


_SHOP_DONE = object()  # sentinel pushed when one adapter finishes


async def run_search(
    conditions: ParsedConditions,
    adapters: list[ShopAdapter],
    *,
    max_results_per_shop: int = 30,
    per_shop_keyword: dict[str, str] | None = None,
) -> AsyncIterator[AdapterEvent]:
    """Fan out to all adapters in parallel and emit AdapterEvents as they arrive.

    Events are produced in arrival order, not adapter order. Cancellation of
    the consumer cancels all in-flight adapter tasks.

    `per_shop_keyword` (when provided) maps shop_slug -> optimized keyword
    string from the LLM query optimizer (#17). Adapters receive a per-shop
    ParsedConditions copy with `keyword_override` set.
    """
    if not adapters:
        yield AdapterEvent(kind="done")
        return

    queue: asyncio.Queue = asyncio.Queue()

    def conditions_for(slug: str) -> ParsedConditions:
        if per_shop_keyword and slug in per_shop_keyword and per_shop_keyword[slug]:
            return conditions.model_copy(update={"keyword_override": per_shop_keyword[slug]})
        return conditions

    async def run_one(adapter: ShopAdapter) -> None:
        await queue.put(AdapterEvent(kind="shop_started", shop_slug=adapter.slug))
        had_error = False
        try:
            async for item in safe_iterate(
                adapter,
                conditions_for(adapter.slug),
                max_results=max_results_per_shop,
            ):
                if item.error:
                    had_error = True
                    await queue.put(
                        AdapterEvent(
                            kind="shop_failed",
                            shop_slug=adapter.slug,
                            message=item.error_message,
                        )
                    )
                else:
                    await queue.put(
                        AdapterEvent(kind="result", shop_slug=adapter.slug, result=item)
                    )
        finally:
            if not had_error:
                await queue.put(
                    AdapterEvent(kind="shop_completed", shop_slug=adapter.slug)
                )
            await queue.put(_SHOP_DONE)

    tasks = [asyncio.create_task(run_one(a)) for a in adapters]
    remaining = len(tasks)
    try:
        while remaining:
            event = await queue.get()
            if event is _SHOP_DONE:
                remaining -= 1
                continue
            yield event
        yield AdapterEvent(kind="done")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
