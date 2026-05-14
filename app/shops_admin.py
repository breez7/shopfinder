"""Shop CRUD helpers used by /admin/shops (issue #24)."""
from __future__ import annotations

import json
from typing import Optional

from sqlmodel import Session, select

from app.adapters.yaml_adapter import validate_yaml_adapter_config
from app.db.models import Shop

BUILTIN_SLUGS = ("eleventh", "gmarket", "musinsa")
YAML_ADAPTER_MODULE = "app.adapters.yaml_adapter:YamlHtmlAdapter"


def is_builtin(slug: str) -> bool:
    return slug in BUILTIN_SLUGS


def list_shops(session: Session) -> list[Shop]:
    return list(session.exec(select(Shop).order_by(Shop.id)).all())


def toggle_enabled(session: Session, slug: str, enabled: bool) -> Optional[Shop]:
    shop = session.exec(select(Shop).where(Shop.slug == slug)).first()
    if shop is None:
        return None
    shop.enabled = enabled
    session.add(shop)
    session.commit()
    session.refresh(shop)
    return shop


def add_yaml_shop(
    session: Session,
    *,
    slug: str,
    name: str,
    config: dict,
) -> tuple[Optional[Shop], list[str]]:
    """Insert a new YAML-declared shop. Returns (shop, errors)."""
    errors: list[str] = []
    slug = (slug or "").strip().lower()
    name = (name or "").strip()
    if not slug:
        errors.append("slug is required")
    if not name:
        errors.append("name is required")
    existing = session.exec(select(Shop).where(Shop.slug == slug)).first()
    if existing is not None:
        errors.append(f"slug already exists: {slug}")
    errors.extend(validate_yaml_adapter_config(config))
    if errors:
        return None, errors

    shop = Shop(
        slug=slug,
        name=name,
        adapter_module=YAML_ADAPTER_MODULE,
        enabled=True,
        config_json=json.dumps(config, ensure_ascii=False),
    )
    session.add(shop)
    session.commit()
    session.refresh(shop)
    return shop, []


def update_yaml_shop_config(
    session: Session, slug: str, config: dict
) -> tuple[Optional[Shop], list[str]]:
    shop = session.exec(select(Shop).where(Shop.slug == slug)).first()
    if shop is None:
        return None, ["shop not found"]
    # Only YAML shops can have their selectors edited
    if shop.adapter_module != YAML_ADAPTER_MODULE:
        return None, ["only YAML-defined shops can be edited"]
    errors = validate_yaml_adapter_config(config)
    if errors:
        return None, errors
    shop.config_json = json.dumps(config, ensure_ascii=False)
    session.add(shop)
    session.commit()
    session.refresh(shop)
    return shop, []


def delete_shop(session: Session, slug: str) -> tuple[bool, Optional[str]]:
    if is_builtin(slug):
        return False, "built-in shops cannot be deleted (only disabled)"
    shop = session.exec(select(Shop).where(Shop.slug == slug)).first()
    if shop is None:
        return False, "shop not found"
    session.delete(shop)
    session.commit()
    return True, None
