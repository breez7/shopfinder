"""Typed wrapper around the `settings` key/value table.

UI form code reads/writes via these helpers so the kv layout stays a private
detail. API keys are stored as-is (single-user self-hosted device) but never
echoed back to the rendered HTML.
"""
from __future__ import annotations

from sqlmodel import Session

from app.db.models import Setting

# Setting keys
KEY_LLM_BASE_URL = "llm_base_url"
KEY_LLM_API_KEY = "llm_api_key"
KEY_LLM_MODEL = "llm_model"
KEY_LLM_CALL_CAP = "llm_call_cap"  # max LLM calls per search; "" or "0" means unlimited
KEY_NAVER_CLIENT_ID = "naver_client_id"
KEY_NAVER_CLIENT_SECRET = "naver_client_secret"


def get(session: Session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    return row.value if (row and row.value is not None) else default


def set_(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
        session.add(row)
    session.commit()


def mask(value: str) -> str:
    """Return a masked rendering safe to put in the page."""
    if not value:
        return ""
    if len(value) <= 6:
        return "*" * len(value)
    return value[:4] + "…" + value[-2:]


def llm_call_cap_int(session: Session) -> int:
    """Return the per-search cap, 0 means unlimited (LM Studio default assumption)."""
    raw = get(session, KEY_LLM_CALL_CAP, "0")
    try:
        return max(0, int(raw))
    except ValueError:
        return 0
