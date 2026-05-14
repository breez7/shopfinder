from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.adapters.naver import NAVER_SEARCH_URL
from app.adapters.types import ParsedConditions
from app.db.models import AdapterWarning
from app.db.session import engine
from app.main import app
from app.warnings import (
    KIND_HTTP_ERROR,
    KIND_PARSE_EXCEPTION,
    clear_all,
    dismiss,
    recent_unresolved,
    record_warning,
)


def _truncate_warnings() -> None:
    with Session(engine) as session:
        for w in session.exec(select(AdapterWarning)).all():
            session.delete(w)
        session.commit()


def test_record_warning_inserts_row() -> None:
    _truncate_warnings()
    record_warning("naver", KIND_HTTP_ERROR, "401")
    with Session(engine) as session:
        rows = session.exec(select(AdapterWarning)).all()
        assert len(rows) == 1
        assert rows[0].shop_slug == "naver"


def test_record_warning_dedupes_identical_within_one_minute() -> None:
    _truncate_warnings()
    record_warning("naver", KIND_HTTP_ERROR, "401")
    record_warning("naver", KIND_HTTP_ERROR, "401")
    record_warning("naver", KIND_HTTP_ERROR, "401")
    with Session(engine) as session:
        rows = session.exec(select(AdapterWarning)).all()
        assert len(rows) == 1


def test_record_warning_does_not_raise_on_internal_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # Telemetry must never break the caller
    from app import warnings as warnings_mod

    def boom(*a, **kw):  # noqa: ARG001
        raise RuntimeError("explode")

    monkeypatch.setattr(warnings_mod, "engine", boom)
    record_warning("x", "x", "x")  # should swallow


def test_recent_unresolved_filters_old_and_dismissed() -> None:
    _truncate_warnings()
    with Session(engine) as session:
        session.add(
            AdapterWarning(
                shop_slug="x", kind="k", message="recent",
                raised_at=datetime.utcnow() - timedelta(days=1),
            )
        )
        session.add(
            AdapterWarning(
                shop_slug="x", kind="k", message="old",
                raised_at=datetime.utcnow() - timedelta(days=30),
            )
        )
        session.add(
            AdapterWarning(
                shop_slug="x", kind="k", message="dismissed",
                dismissed=True,
            )
        )
        session.commit()

        rows = recent_unresolved(session, days=7)
        assert [r.message for r in rows] == ["recent"]


def test_dismiss_marks_row_dismissed() -> None:
    _truncate_warnings()
    with Session(engine) as session:
        w = AdapterWarning(shop_slug="x", kind="k", message="m")
        session.add(w)
        session.commit()
        session.refresh(w)
        assert dismiss(session, w.id)
        refreshed = session.get(AdapterWarning, w.id)
        assert refreshed.dismissed is True


def test_clear_all_returns_deleted_count() -> None:
    _truncate_warnings()
    with Session(engine) as session:
        for i in range(3):
            session.add(AdapterWarning(shop_slug="x", kind="k", message=str(i)))
        session.commit()
        count = clear_all(session)
        assert count == 3
        assert session.exec(select(AdapterWarning)).all() == []


@respx.mock
async def test_naver_500_records_http_error_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    _truncate_warnings()
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    respx.get(NAVER_SEARCH_URL).mock(return_value=httpx.Response(500, text="boom"))

    from app.adapters.naver import NaverAdapter

    adapter = NaverAdapter()
    results = [r async for r in adapter.search(ParsedConditions(category="남방"))]
    assert results[0].error is True

    with Session(engine) as session:
        rows = session.exec(select(AdapterWarning)).all()
        assert len(rows) == 1
        assert rows[0].kind == KIND_HTTP_ERROR
        assert "500" in rows[0].message


def test_index_shows_banner_when_warnings_present() -> None:
    _truncate_warnings()
    with Session(engine) as session:
        session.add(AdapterWarning(shop_slug="naver", kind=KIND_HTTP_ERROR, message="m"))
        session.commit()
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "어댑터 경고" in r.text
        assert "/admin/warnings" in r.text


def test_admin_warnings_page() -> None:
    _truncate_warnings()
    with Session(engine) as session:
        session.add(AdapterWarning(shop_slug="coupang", kind="x", message="alpha"))
        session.commit()
    with TestClient(app) as client:
        r = client.get("/admin/warnings")
        assert r.status_code == 200
        assert "alpha" in r.text
        assert "coupang" in r.text


def test_admin_dismiss_endpoint() -> None:
    _truncate_warnings()
    with Session(engine) as session:
        w = AdapterWarning(shop_slug="x", kind="k", message="m")
        session.add(w)
        session.commit()
        session.refresh(w)
        wid = w.id

    with TestClient(app) as client:
        r = client.post(f"/admin/warnings/{wid}/dismiss")
        assert r.status_code == 200
        r2 = client.post("/admin/warnings/99999/dismiss")
        assert r2.status_code == 404


def test_admin_clear_endpoint() -> None:
    _truncate_warnings()
    with Session(engine) as session:
        for _ in range(3):
            session.add(AdapterWarning(shop_slug="x", kind="k", message="m"))
        session.commit()

    with TestClient(app) as client:
        r = client.post("/admin/warnings/clear")
        assert r.status_code == 200
        assert r.json()["deleted"] == 3
