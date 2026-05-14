from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import ClickLog, SearchHistory
from app.db.session import engine
from app.main import app


def _truncate() -> None:
    with Session(engine) as session:
        for row in session.exec(select(ClickLog)).all():
            session.delete(row)
        for row in session.exec(select(SearchHistory)).all():
            session.delete(row)
        session.commit()


def _add_history(rows: list[tuple[str, datetime]]) -> list[int]:
    ids = []
    with Session(engine) as session:
        for q, when in rows:
            row = SearchHistory(raw_query=q, created_at=when)
            session.add(row)
            session.commit()
            session.refresh(row)
            ids.append(row.id)
    return ids


def test_history_search_filters_by_substring() -> None:
    _truncate()
    now = datetime.utcnow()
    _add_history(
        [
            ("검정 남방", now),
            ("흰색 셔츠", now),
            ("검정 코트", now),
        ]
    )
    with TestClient(app) as client:
        r = client.get("/history?q=검정")
        assert r.status_code == 200
        body = r.text
        assert "검정 남방" in body
        assert "검정 코트" in body
        assert "흰색 셔츠" not in body


def test_history_days_filter() -> None:
    _truncate()
    now = datetime.utcnow()
    _add_history(
        [
            ("recent_zzz", now),
            ("dayten_zzz", now - timedelta(days=10)),
            ("monthago_zzz", now - timedelta(days=100)),
        ]
    )
    with TestClient(app) as client:
        r = client.get("/history?days=7")
        body = r.text
        assert "recent_zzz" in body
        assert "dayten_zzz" not in body


def test_history_delete_endpoint_cascades_clicks() -> None:
    _truncate()
    ids = _add_history([("q1", datetime.utcnow())])
    hid = ids[0]
    with Session(engine) as session:
        session.add(
            ClickLog(search_history_id=hid, shop_slug="naver", result_url="https://x")
        )
        session.commit()

    with TestClient(app) as client:
        r = client.post(f"/history/{hid}/delete")
        assert r.status_code == 200
        # 404 for unknown id
        r2 = client.post("/history/99999/delete")
        assert r2.status_code == 404

    with Session(engine) as session:
        assert session.get(SearchHistory, hid) is None
        clicks = session.exec(select(ClickLog)).all()
        assert clicks == []


def test_history_clear_endpoint_deletes_all() -> None:
    _truncate()
    _add_history([("a", datetime.utcnow()), ("b", datetime.utcnow())])
    with TestClient(app) as client:
        r = client.post("/history/clear")
        assert r.status_code == 200
        assert r.json()["deleted"] == 2
    with Session(engine) as session:
        assert session.exec(select(SearchHistory)).all() == []


def test_history_pagination() -> None:
    _truncate()
    now = datetime.utcnow()
    _add_history([(f"q{i}", now - timedelta(minutes=i)) for i in range(75)])
    with TestClient(app) as client:
        r = client.get("/history?page=1")
        body = r.text
        # Page 1 has 50 rows, latest first
        assert "q0" in body
        assert "q49" in body
        # 50+ rows shouldn't appear on page 1 (since we have 75 rows; rows 50-74 are on page 2)
        assert "q70" not in body
        # Pagination link to page 2
        assert "page=2" in body

        r2 = client.get("/history?page=2")
        assert "q70" in r2.text


def test_history_filters_preserve_in_pagination_links() -> None:
    _truncate()
    now = datetime.utcnow()
    _add_history([(f"blue-{i}", now) for i in range(60)])
    with TestClient(app) as client:
        r = client.get("/history?q=blue&page=1")
        body = r.text
        assert "q=blue" in body  # preserved in pagination link
