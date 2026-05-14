"""Search-result cache keyed by normalized ParsedConditions hash (issue #20)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Optional

from pydantic import TypeAdapter
from sqlmodel import Session, select

from app.adapters.types import ParsedConditions, SearchResult
from app.db.models import SearchResultsCache
from app.db.session import engine

DEFAULT_TTL_HOURS = 24


_results_adapter = TypeAdapter(list[SearchResult])


def conditions_hash(conditions: ParsedConditions) -> str:
    """Deterministic hash of the conditions, with keyword_override stripped
    (the override changes per-shop and shouldn't affect cache identity)."""
    data = conditions.model_dump()
    data.pop("keyword_override", None)
    # Normalize: lower-case strings, drop empty/None values
    normalized: dict[str, object] = {}
    for k, v in data.items():
        if v in (None, "", []):
            continue
        if isinstance(v, str):
            normalized[k] = v.strip().lower()
        else:
            normalized[k] = v
    canon = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def load(hash_key: str) -> Optional[list[SearchResult]]:
    """Return cached results or None when cold/expired."""
    with Session(engine) as session:
        row = session.exec(
            select(SearchResultsCache).where(
                SearchResultsCache.conditions_hash == hash_key
            )
        ).first()
        if row is None:
            return None
        if row.expires_at <= datetime.utcnow():
            # Lazy expiration: prune on read
            session.delete(row)
            session.commit()
            return None
        try:
            return _results_adapter.validate_json(row.payload_json)
        except Exception:  # noqa: BLE001
            # Corrupt payload — drop and treat as miss
            session.delete(row)
            session.commit()
            return None


def store(
    hash_key: str,
    results: list[SearchResult],
    *,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> None:
    payload = _results_adapter.dump_json(results).decode("utf-8")
    now = datetime.utcnow()
    expires = now + timedelta(hours=ttl_hours)
    with Session(engine) as session:
        existing = session.exec(
            select(SearchResultsCache).where(
                SearchResultsCache.conditions_hash == hash_key
            )
        ).first()
        if existing is None:
            session.add(
                SearchResultsCache(
                    conditions_hash=hash_key,
                    payload_json=payload,
                    expires_at=expires,
                )
            )
        else:
            existing.payload_json = payload
            existing.created_at = now
            existing.expires_at = expires
            session.add(existing)
        session.commit()


def purge_expired() -> int:
    """Optional housekeeping. Returns rows removed."""
    with Session(engine) as session:
        rows = session.exec(
            select(SearchResultsCache).where(
                SearchResultsCache.expires_at <= datetime.utcnow()
            )
        ).all()
        for r in rows:
            session.delete(r)
        session.commit()
        return len(rows)
