from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.adapters.base import ShopAdapter, safe_iterate
from app.adapters.registry import _load_class, load_enabled_adapters
from app.adapters.types import ParsedConditions, SearchResult
from app.db.models import Shop
from app.db.seed import seed_defaults


class FakeAdapter(ShopAdapter):
    slug = "fake"
    display_name = "Fake"

    async def search(
        self,
        conditions: ParsedConditions,
        max_results: int = 30,
    ) -> AsyncIterator[SearchResult]:
        for i in range(3):
            yield SearchResult(
                shop_slug=self.slug,
                title=f"item-{i}",
                price=1000 * (i + 1),
                product_url=f"https://example.com/{i}",
            )


class BrokenAdapter(ShopAdapter):
    slug = "broken"

    async def search(
        self,
        conditions: ParsedConditions,
        max_results: int = 30,
    ) -> AsyncIterator[SearchResult]:
        yield SearchResult(shop_slug=self.slug, title="one", product_url="x")
        raise RuntimeError("explode mid-iteration")


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


async def test_fake_adapter_yields_n_results() -> None:
    adapter = FakeAdapter()
    results = []
    async for r in adapter.search(ParsedConditions()):
        results.append(r)
    assert len(results) == 3
    assert results[0].title == "item-0"


async def test_safe_iterate_catches_midstream_exception() -> None:
    adapter = BrokenAdapter()
    results = []
    async for r in safe_iterate(adapter, ParsedConditions()):
        results.append(r)
    # one normal + one error result
    assert len(results) == 2
    assert results[0].error is False
    assert results[1].error is True
    assert "RuntimeError" in (results[1].error_message or "")


def test_load_class_resolves_module_path() -> None:
    cls = _load_class("tests.test_adapter_interface:FakeAdapter")
    assert cls is FakeAdapter


def test_load_enabled_adapters_skips_missing_modules(session: Session) -> None:
    seed_defaults(session)
    # Default seed has only naver enabled. Naver module doesn't exist yet (#6).
    adapters = load_enabled_adapters(session)
    assert adapters == []


def test_load_enabled_adapters_loads_present_modules(session: Session) -> None:
    seed_defaults(session)
    # Inject a Shop pointing at our test FakeAdapter
    fake_shop = Shop(
        slug="fake",
        name="Fake",
        adapter_module="tests.test_adapter_interface:FakeAdapter",
        enabled=True,
    )
    session.add(fake_shop)
    session.commit()

    adapters = load_enabled_adapters(session)
    assert len(adapters) == 1
    assert adapters[0].slug == "fake"


def test_parsed_conditions_keyword_concats_known_fields() -> None:
    p = ParsedConditions(
        category="남방",
        color="검정",
        size="100",
        material="폴리에스테르",
        fit="루즈핏",
        free_text="신상",
    )
    kw = p.keyword()
    assert "검정" in kw
    assert "남방" in kw
    assert "루즈핏" in kw
    assert "신상" in kw


def test_search_result_make_error() -> None:
    err = SearchResult.make_error(shop_slug="x", message="boom")
    assert err.error is True
    assert err.error_message == "boom"
