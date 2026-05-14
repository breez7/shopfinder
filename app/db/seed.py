from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import SchemaVersion, Shop

CURRENT_SCHEMA_VERSION = 1

DEFAULT_SHOPS: list[dict] = [
    {
        "slug": "eleventh",
        "name": "11번가",
        "adapter_module": "app.adapters.eleventh:ElevenstAdapter",
        "enabled": True,
        "config_json": "{}",
    },
    {
        "slug": "gmarket",
        "name": "G마켓",
        "adapter_module": "app.adapters.gmarket:GmarketAdapter",
        "enabled": True,
        "config_json": "{}",
    },
    {
        "slug": "musinsa",
        "name": "무신사",
        "adapter_module": "app.adapters.musinsa:MusinsaAdapter",
        "enabled": True,
        "config_json": "{}",
    },
]


def seed_defaults(session: Session) -> None:
    existing_version = session.get(SchemaVersion, CURRENT_SCHEMA_VERSION)
    if existing_version is None:
        session.add(SchemaVersion(version=CURRENT_SCHEMA_VERSION))

    # Drop shops that used to be seeded by default but were retired
    # (kept their adapter modules around so they can be re-added through
    # the /admin/shops UI as YAML if needed).
    RETIRED = {"naver", "coupang"}
    for shop in session.exec(select(Shop).where(Shop.slug.in_(RETIRED))).all():
        session.delete(shop)

    existing_slugs = set(session.exec(select(Shop.slug)).all())
    for entry in DEFAULT_SHOPS:
        if entry["slug"] in existing_slugs:
            continue
        session.add(Shop(**entry))

    session.commit()
