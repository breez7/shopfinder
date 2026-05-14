from __future__ import annotations

import json

import httpx
import respx
from fastapi.testclient import TestClient
from httpx import ASGITransport
from sqlmodel import Session, select

from app.adapters.registry import load_enabled_adapters
from app.adapters.yaml_adapter import YamlHtmlAdapter, validate_yaml_adapter_config
from app.db.models import Shop
from app.db.session import engine
from app.main import app
from app.shops_admin import (
    BUILTIN_SLUGS,
    YAML_ADAPTER_MODULE,
    add_yaml_shop,
    delete_shop,
    is_builtin,
    toggle_enabled,
    update_yaml_shop_config,
)


def _clean_custom_shops() -> None:
    with Session(engine) as session:
        for s in session.exec(select(Shop)).all():
            if not is_builtin(s.slug):
                session.delete(s)
            else:
                # Reset built-in state for predictable tests
                s.enabled = True
                session.add(s)
        session.commit()


def test_validate_yaml_adapter_config_requires_keys_and_keyword_placeholder() -> None:
    errors = validate_yaml_adapter_config({})
    assert any("search_url_template" in e for e in errors)
    errors = validate_yaml_adapter_config(
        {
            "search_url_template": "https://example.com/search?q=fixed",
            "card_selector": "li",
            "title_selector": "t",
            "price_selector": "p",
            "link_selector": "a",
        }
    )
    assert any("{keyword}" in e for e in errors)
    errors = validate_yaml_adapter_config(
        {
            "search_url_template": "https://example.com/search?q={keyword}",
            "card_selector": "li",
            "title_selector": "t",
            "price_selector": "p",
            "link_selector": "a",
        }
    )
    assert errors == []


def test_is_builtin() -> None:
    for slug in BUILTIN_SLUGS:
        assert is_builtin(slug)
    assert not is_builtin("custom-shop")


def test_add_yaml_shop_inserts_row_and_appears_in_registry() -> None:
    _clean_custom_shops()
    with Session(engine) as session:
        shop, errors = add_yaml_shop(
            session,
            slug="custom1",
            name="My Custom",
            config={
                "search_url_template": "https://example.com/?q={keyword}",
                "card_selector": "li",
                "title_selector": ".t",
                "price_selector": ".p",
                "link_selector": "a",
            },
        )
        assert errors == []
        assert shop is not None
        assert shop.adapter_module == YAML_ADAPTER_MODULE

        adapters = load_enabled_adapters(session)
        slugs = [a.slug for a in adapters]
        assert "custom1" in slugs


def test_add_yaml_shop_returns_errors_for_invalid_input() -> None:
    _clean_custom_shops()
    with Session(engine) as session:
        _, errors = add_yaml_shop(session, slug="", name="x", config={})
        assert any("slug" in e for e in errors)


def test_add_yaml_shop_rejects_duplicate_slug() -> None:
    _clean_custom_shops()
    cfg = {
        "search_url_template": "https://x/?q={keyword}",
        "card_selector": "li",
        "title_selector": "t",
        "price_selector": "p",
        "link_selector": "a",
    }
    with Session(engine) as session:
        add_yaml_shop(session, slug="dupe", name="x", config=cfg)
        _, errors = add_yaml_shop(session, slug="dupe", name="x", config=cfg)
        assert any("already exists" in e for e in errors)


def test_toggle_enabled() -> None:
    _clean_custom_shops()
    with Session(engine) as session:
        toggle_enabled(session, "musinsa", False)
        shop = next(s for s in session.exec(select(Shop)).all() if s.slug == "musinsa")
        assert shop.enabled is False


def test_delete_shop_refuses_built_in() -> None:
    _clean_custom_shops()
    with Session(engine) as session:
        ok, err = delete_shop(session, "musinsa")
        assert ok is False
        assert "built-in" in (err or "")


def test_delete_shop_removes_custom_row() -> None:
    _clean_custom_shops()
    cfg = {
        "search_url_template": "https://x/?q={keyword}",
        "card_selector": "li",
        "title_selector": "t",
        "price_selector": "p",
        "link_selector": "a",
    }
    with Session(engine) as session:
        add_yaml_shop(session, slug="todelete", name="x", config=cfg)
        ok, _ = delete_shop(session, "todelete")
        assert ok is True
        assert (
            session.exec(select(Shop).where(Shop.slug == "todelete")).first() is None
        )


def test_admin_shops_page_renders_with_builtins() -> None:
    _clean_custom_shops()
    with TestClient(app) as client:
        r = client.get("/admin/shops")
        assert r.status_code == 200
        for slug in BUILTIN_SLUGS:
            assert slug in r.text
        assert "기본" in r.text  # built-in marker


def test_admin_shops_add_via_yaml_blob() -> None:
    _clean_custom_shops()
    yaml_blob = """search_url_template: "https://www.example.com/search?q={keyword}"
card_selector: "li.product"
title_selector: ".t"
price_selector: ".p"
link_selector: "a"
image_selector: "img"
specs_selector: ""
"""
    with TestClient(app) as client:
        r = client.post(
            "/admin/shops/add",
            data={"slug": "viayaml", "name": "Via YAML", "config": yaml_blob},
        )
        assert r.status_code == 200
        assert "추가됨" in r.text

    with Session(engine) as session:
        shop = session.exec(select(Shop).where(Shop.slug == "viayaml")).first()
        assert shop is not None
        cfg = json.loads(shop.config_json)
        assert cfg["card_selector"] == "li.product"


def test_admin_shops_add_via_json_blob() -> None:
    _clean_custom_shops()
    blob = json.dumps(
        {
            "search_url_template": "https://x/?q={keyword}",
            "card_selector": "li",
            "title_selector": "t",
            "price_selector": "p",
            "link_selector": "a",
        }
    )
    with TestClient(app) as client:
        r = client.post(
            "/admin/shops/add",
            data={"slug": "viajson", "name": "Via JSON", "config": blob},
        )
        assert r.status_code == 200
        assert "추가됨" in r.text


def test_admin_shops_add_surfaces_validation_errors() -> None:
    _clean_custom_shops()
    bad_yaml = "search_url_template: https://x/no-placeholder"
    with TestClient(app) as client:
        r = client.post(
            "/admin/shops/add",
            data={"slug": "bad", "name": "Bad", "config": bad_yaml},
        )
        assert r.status_code == 200
        assert "{keyword}" in r.text or "missing" in r.text


def test_admin_shops_toggle_endpoint_flips_state() -> None:
    _clean_custom_shops()
    with TestClient(app) as client:
        r = client.post("/admin/shops/musinsa/toggle")
        assert r.status_code == 200
    with Session(engine) as session:
        shop = next(s for s in session.exec(select(Shop)).all() if s.slug == "musinsa")
        assert shop.enabled is False  # we started enabled, flipped once


def test_admin_shops_delete_built_in_returns_400() -> None:
    with TestClient(app) as client:
        r = client.post("/admin/shops/musinsa/delete")
        assert r.status_code == 400


@respx.mock
async def test_yaml_adapter_actually_searches() -> None:
    """End-to-end: add a YAML shop, run /search/stream — that shop participates."""
    _clean_custom_shops()
    with Session(engine) as session:
        # Disable built-ins so only the custom shop produces results
        for s in session.exec(select(Shop)).all():
            s.enabled = False
            session.add(s)
        session.commit()
        add_yaml_shop(
            session,
            slug="exampleshop",
            name="Example",
            config={
                "search_url_template": "https://example.com/search?q={keyword}",
                "card_selector": "li.product",
                "title_selector": ".title",
                "price_selector": ".price",
                "link_selector": "a",
                "image_selector": "img",
                "specs_selector": "",
            },
        )

    # Speed up jitter
    YamlHtmlAdapter.min_delay_s = 0
    YamlHtmlAdapter.max_delay_s = 0

    respx.get(url__regex=r"https?://example\.com/search.*").mock(
        return_value=httpx.Response(
            200,
            text=(
                "<html><body>"
                "<li class='product'>"
                '<a href="https://example.com/p/1"><img src="//img/1.jpg"></a>'
                '<div class="title">YAML 검정 셔츠</div>'
                '<div class="price">12,300</div>'
                "</li></body></html>"
            ),
        )
    )

    saw_result_from_custom = False
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/search/stream?q=검정 셔츠") as response:
            async for line in response.aiter_lines():
                if 'data-shop="exampleshop"' in line:
                    saw_result_from_custom = True
    assert saw_result_from_custom

    # Cleanup so other tests don't trip over the disabled built-ins
    _clean_custom_shops()
