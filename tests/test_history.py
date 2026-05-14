from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient
from httpx import ASGITransport
from sqlmodel import Session, select

from app.db.models import ClickLog, SearchHistory
from app.db.session import engine
from app.main import app


def _truncate_history() -> None:
    with Session(engine) as session:
        for row in session.exec(select(ClickLog)).all():
            session.delete(row)
        for row in session.exec(select(SearchHistory)).all():
            session.delete(row)
        session.commit()


def test_history_page_empty() -> None:
    _truncate_history()
    with TestClient(app) as client:
        r = client.get("/history")
        assert r.status_code == 200
        assert "아직 검색 기록이 없습니다" in r.text


async def test_search_stream_creates_history_row() -> None:
    _truncate_history()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/search/stream?q=검정 남방") as response:
            assert response.status_code == 200
            async for _ in response.aiter_text():
                pass  # drain to completion

    with Session(engine) as session:
        rows = session.exec(select(SearchHistory)).all()
        assert len(rows) == 1
        assert rows[0].raw_query == "검정 남방"
        assert rows[0].parsed_by == "regex"
        # elapsed_ms is set even when there are no adapters
        assert rows[0].elapsed_ms >= 0


def test_click_endpoint_logs_against_history() -> None:
    _truncate_history()
    with Session(engine) as session:
        hist = SearchHistory(raw_query="abc")
        session.add(hist)
        session.commit()
        session.refresh(hist)
        history_id = hist.id

    with TestClient(app) as client:
        r = client.post(
            "/click",
            data={
                "history_id": str(history_id),
                "shop_slug": "naver",
                "product_url": "https://x/p/1",
            },
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    with Session(engine) as session:
        clicks = session.exec(select(ClickLog)).all()
        assert len(clicks) == 1
        assert clicks[0].search_history_id == history_id
        assert clicks[0].shop_slug == "naver"


def test_click_endpoint_404_for_unknown_history() -> None:
    _truncate_history()
    with TestClient(app) as client:
        r = client.post(
            "/click",
            data={
                "history_id": "99999",
                "shop_slug": "naver",
                "product_url": "x",
            },
        )
        assert r.status_code == 404


def test_history_page_lists_recent_searches() -> None:
    _truncate_history()
    with Session(engine) as session:
        for i in range(3):
            session.add(SearchHistory(raw_query=f"query-{i}", total_results=i))
        session.commit()

    with TestClient(app) as client:
        r = client.get("/history")
        assert r.status_code == 200
        for i in range(3):
            assert f"query-{i}" in r.text
        # replay link present
        assert "재실행" in r.text


def test_index_accepts_q_param_for_replay() -> None:
    with TestClient(app) as client:
        r = client.get("/?q=검정 남방")
        assert r.status_code == 200
        # The input value is pre-filled
        assert 'value="검정 남방"' in r.text
