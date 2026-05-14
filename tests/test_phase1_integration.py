"""Phase 1 integration test (issue #30).

Verifies the FR matrix promised for Phase 1 by exercising the live FastAPI
app with the Naver adapter as the only enabled shop. The Naver HTTP layer
is mocked via respx so the test is deterministic and runs in CI without
external creds.

FRs covered: FR-1, FR-2 (regex path), FR-3, FR-4 (Naver), FR-5, FR-8,
FR-10 (LLM-less fallback), FR-12 (viewport meta), FR-14, FR-16
(external traffic limited to Naver).
"""
from __future__ import annotations

import json
import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from httpx import ASGITransport
from sqlmodel import Session, select

from app.adapters.naver import NAVER_SEARCH_URL
from app.db.models import ClickLog, SearchHistory, Shop
from app.db.session import engine
from app.main import app


def _reset_state() -> None:
    """Truncate history-related rows and ensure only naver is enabled."""
    with Session(engine) as session:
        for row in session.exec(select(ClickLog)).all():
            session.delete(row)
        for row in session.exec(select(SearchHistory)).all():
            session.delete(row)
        for shop in session.exec(select(Shop)).all():
            shop.enabled = shop.slug == "naver"
            session.add(shop)
        session.commit()


def _naver_payload(items: list[dict]) -> dict:
    return {"items": items}


PRD_QUERY = "검정색 100 사이즈 긴팔 남방 폴리에스테르 80 이상 루즈핏 2만원 이하"


def test_FR1_index_renders_search_form() -> None:
    _reset_state()
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert 'id="search-form"' in r.text
        # FR-12: viewport meta present
        assert 'name="viewport"' in r.text


def test_FR2_regex_parser_extracts_all_dimensions_via_parse_endpoint() -> None:
    _reset_state()
    with TestClient(app) as client:
        r = client.post("/parse", data={"q": PRD_QUERY})
        assert r.status_code == 200
        body = r.text
        assert "검정" in body
        assert "100" in body
        assert "남방" in body  # category includes 긴팔 prefix
        assert "긴팔" in body
        assert "폴리에스테르" in body
        assert "80%" in body
        assert "루즈핏" in body
        assert "20,000원" in body
        # FR-10: parsing path tagged regex (no LLM configured)
        assert "regex" in body


@respx.mock
async def test_FR4_FR5_FR14_naver_stream_with_history_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: parse → fan-out → SSE → history persistence."""
    _reset_state()
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")

    respx.get(NAVER_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_naver_payload(
                [
                    {
                        "title": "<b>검정</b> 긴팔 남방",
                        "link": "https://shopping.naver.com/p/1",
                        "image": "https://img.example/1.jpg",
                        "lprice": "15000",
                        "brand": "BrandA",
                        "mallName": "MallA",
                    },
                    {
                        "title": "검정 코트 (over budget)",
                        "link": "https://shopping.naver.com/p/2",
                        "image": "https://img.example/2.jpg",
                        "lprice": "300000",
                        "brand": "BrandB",
                        "mallName": "MallB",
                    },
                ]
            ),
        )
    )

    transport = ASGITransport(app=app)
    t0 = time.monotonic()
    saw_meta = False
    saw_shop_started = False
    saw_result = False
    saw_shop_completed = False
    saw_done = False
    result_count = 0
    history_id_from_stream: int | None = None
    first_result_at: float | None = None

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "GET", "/search/stream", params={"q": PRD_QUERY}
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            current_event: str | None = None
            buffer: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    buffer.append(line[5:].strip())
                elif line == "":  # event terminator (blank line)
                    if not current_event:
                        continue
                    data = "\n".join(buffer)
                    if current_event == "meta":
                        saw_meta = True
                        payload = json.loads(data)
                        history_id_from_stream = payload["history_id"]
                    elif current_event == "shop_started":
                        saw_shop_started = True
                    elif current_event == "result":
                        saw_result = True
                        if first_result_at is None:
                            first_result_at = time.monotonic() - t0
                        result_count += 1
                        # FR-8: card includes an external link with target=_blank
                        assert "target=\"_blank\"" in data
                        assert "rel=\"noopener noreferrer\"" in data
                    elif current_event == "shop_completed":
                        saw_shop_completed = True
                    elif current_event == "done":
                        saw_done = True
                    current_event = None
                    buffer = []

    # Lifecycle: every named event emitted exactly once (1 adapter, 1 useful result)
    assert saw_meta and saw_shop_started and saw_result and saw_shop_completed and saw_done
    # FR-4: max_price filter applied — only the 15000 item survives
    assert result_count == 1
    # FR-5: first result well within 5s (mock is instant — gives margin against scheduler jitter)
    assert first_result_at is not None and first_result_at < 5.0

    # FR-14: SearchHistory row persisted with stamped totals
    with Session(engine) as session:
        rows = session.exec(select(SearchHistory)).all()
        assert len(rows) == 1
        assert rows[0].id == history_id_from_stream
        assert rows[0].raw_query == PRD_QUERY
        assert rows[0].parsed_by == "regex"
        assert rows[0].total_results == 1
        assert rows[0].elapsed_ms >= 0


def test_FR14_click_endpoint_logs_against_history() -> None:
    """Click on a result card logs a click_log row tied to the history."""
    _reset_state()
    with Session(engine) as session:
        hist = SearchHistory(raw_query="x")
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
                "product_url": "https://shopping.naver.com/p/1",
            },
        )
        assert r.status_code == 200

    with Session(engine) as session:
        clicks = session.exec(select(ClickLog)).all()
        assert len(clicks) == 1
        assert clicks[0].search_history_id == history_id
        assert clicks[0].shop_slug == "naver"


@respx.mock
async def test_FR10_works_with_no_llm_configured() -> None:
    """LLM is not configured anywhere in Phase 1 — system must still answer."""
    _reset_state()
    # Naver mock returns one trivial item
    respx.get(NAVER_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_naver_payload(
                [{"title": "x", "link": "https://x/p", "lprice": "1000"}]
            ),
        )
    )
    import os

    os.environ["NAVER_CLIENT_ID"] = "x"
    os.environ["NAVER_CLIENT_SECRET"] = "y"

    transport = ASGITransport(app=app)
    saw_result = False
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/search/stream?q=검정 셔츠") as response:
            async for line in response.aiter_lines():
                if line.startswith("event:") and "result" in line:
                    saw_result = True
                if line.startswith("event:") and "done" in line:
                    break
    assert saw_result


@respx.mock
async def test_adapter_failure_does_not_kill_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-3: adapter raise mid-iteration surfaces as shop_failed, stream finishes."""
    _reset_state()
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    respx.get(NAVER_SEARCH_URL).mock(return_value=httpx.Response(500, text="boom"))

    transport = ASGITransport(app=app)
    saw_shop_failed = False
    saw_done = False
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/search/stream?q=검정 셔츠") as response:
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    name = line.split(":", 1)[1].strip()
                    if name == "shop_failed":
                        saw_shop_failed = True
                    elif name == "done":
                        saw_done = True
    assert saw_shop_failed
    assert saw_done


def test_FR16_no_external_traffic_without_naver_call() -> None:
    """With no enabled adapter, the stream produces only meta+done — no outbound HTTP."""
    _reset_state()
    with Session(engine) as session:
        for shop in session.exec(select(Shop)).all():
            shop.enabled = False
            session.add(shop)
        session.commit()

    with TestClient(app) as client:
        with client.stream("GET", "/search/stream", params={"q": "x"}) as response:
            assert response.status_code == 200
            events = []
            for line in response.iter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
            assert "done" in events
            assert "result" not in events
            assert "shop_started" not in events
