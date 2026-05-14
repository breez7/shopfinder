from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
from httpx import ASGITransport

from app.adapters.base import ShopAdapter
from app.adapters.types import ParsedConditions, SearchResult
from app.main import app
from app.search import run_search


class _FakeAdapter(ShopAdapter):
    slug = "fakeA"

    def __init__(self, results: list[SearchResult], delay: float = 0):
        super().__init__()
        self._results = results
        self._delay = delay

    async def search(
        self,
        conditions: ParsedConditions,
        max_results: int = 30,
    ) -> AsyncIterator[SearchResult]:
        for r in self._results:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield r


class _ErroringAdapter(ShopAdapter):
    slug = "fakeB"

    async def search(
        self,
        conditions: ParsedConditions,
        max_results: int = 30,
    ) -> AsyncIterator[SearchResult]:
        yield SearchResult.make_error(self.slug, "boom")


async def test_run_search_emits_lifecycle_events_for_one_adapter() -> None:
    a = _FakeAdapter([
        SearchResult(shop_slug="fakeA", title="t1", product_url="u1"),
        SearchResult(shop_slug="fakeA", title="t2", product_url="u2"),
    ])
    a.slug = "fakeA"
    events = []
    async for e in run_search(ParsedConditions(), [a]):
        events.append(e.kind)
    assert events == ["shop_started", "result", "result", "shop_completed", "done"]


async def test_run_search_fans_out_to_multiple_adapters() -> None:
    a = _FakeAdapter([SearchResult(shop_slug="A", title="a", product_url="ua")])
    a.slug = "A"
    b = _FakeAdapter([SearchResult(shop_slug="B", title="b", product_url="ub")])
    b.slug = "B"
    kinds = []
    async for e in run_search(ParsedConditions(), [a, b]):
        kinds.append(e.kind)
    # both adapters should produce started + result + completed, then a single done
    assert kinds.count("shop_started") == 2
    assert kinds.count("result") == 2
    assert kinds.count("shop_completed") == 2
    assert kinds[-1] == "done"


async def test_run_search_with_failing_adapter_emits_shop_failed() -> None:
    e = _ErroringAdapter()
    e.slug = "fakeB"
    events = [ev async for ev in run_search(ParsedConditions(), [e])]
    kinds = [ev.kind for ev in events]
    assert "shop_failed" in kinds
    assert kinds[-1] == "done"
    # shop_completed should NOT be emitted when an error occurred
    assert "shop_completed" not in kinds


async def test_run_search_with_no_adapters_emits_only_done() -> None:
    events = [ev async for ev in run_search(ParsedConditions(), [])]
    assert [e.kind for e in events] == ["done"]


async def test_sse_endpoint_streams_events_for_empty_query() -> None:
    """Hit the actual /search/stream endpoint. With no adapter modules loadable,
    we should still get a `done` event quickly."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/search/stream?q=") as response:
            assert response.status_code == 200
            saw_done = False
            async for chunk in response.aiter_text():
                if "event: done" in chunk or "\nevent:done" in chunk or chunk.startswith("event: done"):
                    saw_done = True
                    break
                if "data:" in chunk and "done" in chunk:
                    saw_done = True
                    break
            assert saw_done
