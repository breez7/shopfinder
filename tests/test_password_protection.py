from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.auth import (
    COOKIE_NAME,
    KEY_ACCESS_PASSWORD,
    auth_enabled,
    check_password,
    make_session_token,
    rate_limit_clear,
    rate_limit_is_locked,
    rate_limit_record_attempt,
    set_password,
    validate_session,
)
from app.db.models import Setting
from app.db.session import engine
from app.main import app


def _clear_password() -> None:
    with Session(engine) as session:
        for row in session.exec(select(Setting)).all():
            if row.key == KEY_ACCESS_PASSWORD:
                session.delete(row)
        session.commit()


def test_auth_disabled_when_no_password() -> None:
    _clear_password()
    assert auth_enabled() is False
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200  # no redirect


def test_auth_enabled_after_password_set() -> None:
    _clear_password()
    set_password("secret123")
    assert auth_enabled() is True
    _clear_password()


def test_check_password_constant_time() -> None:
    _clear_password()
    set_password("topsecret")
    assert check_password("topsecret") is True
    assert check_password("nope") is False
    _clear_password()


def test_session_token_round_trip() -> None:
    token = make_session_token()
    assert validate_session(token) is True
    assert validate_session(None) is False
    assert validate_session("garbage") is False


def test_protected_routes_redirect_to_login_when_unauthenticated() -> None:
    _clear_password()
    set_password("p1")
    try:
        with TestClient(app) as client:
            r = client.get("/", follow_redirects=False)
            assert r.status_code == 303
            assert r.headers["location"] == "/login"
            # healthz remains open
            r2 = client.get("/healthz")
            assert r2.status_code == 200
    finally:
        _clear_password()


def test_login_grants_cookie_and_then_pages_load() -> None:
    _clear_password()
    set_password("alphabeta")
    try:
        with TestClient(app) as client:
            # Wrong password
            bad = client.post("/login", data={"password": "wrong"})
            assert bad.status_code == 401
            # Correct password sets cookie
            ok = client.post("/login", data={"password": "alphabeta"}, follow_redirects=False)
            assert ok.status_code == 303
            assert COOKIE_NAME in ok.cookies
            # Subsequent request uses the cookie
            page = client.get("/")
            assert page.status_code == 200
    finally:
        _clear_password()


def test_logout_clears_cookie() -> None:
    _clear_password()
    set_password("ab12")
    try:
        with TestClient(app) as client:
            client.post("/login", data={"password": "ab12"}, follow_redirects=False)
            logout = client.post("/logout", follow_redirects=False)
            assert logout.status_code == 303
            # Cookie cleared — next request redirects again
            again = client.get("/", follow_redirects=False)
            assert again.status_code == 303
    finally:
        _clear_password()


def test_rate_limit_records_attempts_and_locks_after_threshold() -> None:
    ip = "10.0.0.1"
    # Burn through 5 attempts
    for _ in range(5):
        rate_limit_record_attempt(ip)
    assert rate_limit_is_locked(ip)
    rate_limit_clear(ip)
    assert not rate_limit_is_locked(ip)
