"""Optional single-password gateway for external exposure (issue #28).

When `access_password` is empty in the settings table, the gateway is
disabled and every route serves anonymously (current behavior).
When set, every route except `/healthz`, `/login`, and `/static/*` requires
a valid signed session cookie.
"""
from __future__ import annotations

import hmac
import time
from collections import deque
from typing import Optional

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlmodel import Session

from app.config import get_settings
from app.db.session import engine
from app.settings_store import get as settings_get
from app.settings_store import set_ as settings_set

KEY_ACCESS_PASSWORD = "access_password"
COOKIE_NAME = "shopfinder_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days

# in-memory failed-attempt counters: deque of timestamps per IP
_FAILED: dict[str, deque[float]] = {}
RATE_LIMIT_WINDOW_S = 60
RATE_LIMIT_MAX = 5


def serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="shopfinder-session")


def get_password() -> str:
    with Session(engine) as session:
        return settings_get(session, KEY_ACCESS_PASSWORD)


def set_password(new_password: str) -> None:
    with Session(engine) as session:
        settings_set(session, KEY_ACCESS_PASSWORD, new_password)


def auth_enabled() -> bool:
    return bool(get_password())


def check_password(submitted: str) -> bool:
    """Constant-time comparison."""
    stored = get_password()
    if not stored:
        return False
    return hmac.compare_digest(stored.encode("utf-8"), submitted.encode("utf-8"))


def make_session_token() -> str:
    return serializer().dumps({"ok": True, "iat": int(time.time())})


def validate_session(token: Optional[str]) -> bool:
    if not token:
        return False
    try:
        data = serializer().loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except BadSignature:
        return False
    return isinstance(data, dict) and data.get("ok") is True


def rate_limit_record_attempt(ip: str) -> int:
    """Record one failed attempt for `ip`. Returns the count within the window."""
    now = time.time()
    q = _FAILED.setdefault(ip, deque())
    while q and q[0] < now - RATE_LIMIT_WINDOW_S:
        q.popleft()
    q.append(now)
    return len(q)


def rate_limit_is_locked(ip: str) -> bool:
    now = time.time()
    q = _FAILED.get(ip)
    if not q:
        return False
    while q and q[0] < now - RATE_LIMIT_WINDOW_S:
        q.popleft()
    return len(q) >= RATE_LIMIT_MAX


def rate_limit_clear(ip: str) -> None:
    _FAILED.pop(ip, None)


# Public unprotected path prefixes
OPEN_PATHS = ("/healthz", "/login", "/static/")
