from __future__ import annotations

import importlib
import json
from typing import Optional

from sqlmodel import Session, select

from app.adapters.base import ShopAdapter
from app.db.models import Shop


def _load_class(adapter_module: str) -> type[ShopAdapter]:
    """Resolve a 'pkg.mod:ClassName' spec into the class object."""
    if ":" not in adapter_module:
        raise ValueError(f"Adapter spec must be 'module:Class', got: {adapter_module!r}")
    mod_path, cls_name = adapter_module.split(":", 1)
    module = importlib.import_module(mod_path)
    return getattr(module, cls_name)


def load_enabled_adapters(
    session: Session,
    *,
    skip_missing: bool = True,
) -> list[ShopAdapter]:
    """Instantiate every enabled shop's adapter.

    If `skip_missing=True`, an adapter whose module is not yet implemented is silently
    skipped (Phase 1 reality: only Naver exists, the other 4 modules land in Phase 2).
    """
    shops = session.exec(select(Shop).where(Shop.enabled == True)).all()  # noqa: E712
    adapters: list[ShopAdapter] = []
    for shop in shops:
        try:
            cls = _load_class(shop.adapter_module)
        except (ImportError, AttributeError):
            if skip_missing:
                continue
            raise
        config = json.loads(shop.config_json or "{}")
        adapter = cls(config=config)
        adapter.slug = shop.slug
        adapter.display_name = shop.name
        adapters.append(adapter)
    return adapters


def load_adapter_by_slug(session: Session, slug: str) -> Optional[ShopAdapter]:
    shop = session.exec(select(Shop).where(Shop.slug == slug)).first()
    if shop is None:
        return None
    cls = _load_class(shop.adapter_module)
    adapter = cls(config=json.loads(shop.config_json or "{}"))
    adapter.slug = shop.slug
    adapter.display_name = shop.name
    return adapter
