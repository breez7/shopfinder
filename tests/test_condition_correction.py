from __future__ import annotations

import json

import httpx
import respx
from fastapi.testclient import TestClient
from httpx import ASGITransport
from sqlmodel import Session, select

from app.adapters.naver import NAVER_SEARCH_URL
from app.db.models import Setting, Shop
from app.db.session import engine
from app.main import app


def _enable_only_naver() -> None:
    with Session(engine) as session:
        for s in session.exec(select(Shop)).all():
            s.enabled = s.slug == "naver"
            session.add(s)
        session.commit()


def _clear_settings() -> None:
    with Session(engine) as session:
        for row in session.exec(select(Setting)).all():
            session.delete(row)
        session.commit()


def test_parse_endpoint_returns_editable_form() -> None:
    with TestClient(app) as client:
        r = client.post("/parse", data={"q": "검정 100 남방 2만원 이하"})
        assert r.status_code == 200
        body = r.text
        assert 'name="color"' in body
        assert 'name="size"' in body
        assert 'name="max_price"' in body
        assert 'id="apply-edits"' in body
        assert 'id="reset-edits"' in body
        # Original parsed values embedded for the reset action
        assert 'data-original=' in body


def test_parse_endpoint_prefills_inputs_with_parsed_values() -> None:
    with TestClient(app) as client:
        r = client.post("/parse", data={"q": "검정 100 남방 2만원 이하"})
        body = r.text
        # color input pre-filled
        assert 'name="color" value="검정"' in body
        # size input pre-filled
        assert 'name="size" value="100"' in body
        # max_price input pre-filled
        assert 'name="max_price"' in body and '20000' in body


@respx.mock
async def test_use_edits_bypasses_parser(monkeypatch) -> None:
    _enable_only_naver()
    _clear_settings()
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    respx.get(NAVER_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"title": "x", "link": "https://x/p", "lprice": "9000"}]},
        )
    )

    transport = ASGITransport(app=app)
    saw_result = False
    parsed_by_val = None
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "GET",
            "/search/stream?use_edits=1&color=흰색&category=셔츠&max_price=10000",
        ) as response:
            current = None
            buf = []
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    current = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    buf.append(line[5:].strip())
                elif line == "" and current:
                    data = "\n".join(buf)
                    if current == "meta":
                        parsed_by_val = json.loads(data).get("parsed_by")
                    elif current == "result":
                        saw_result = True
                    current = None
                    buf = []

    assert parsed_by_val == "edited"
    assert saw_result

    # Naver received the edited keyword, not whatever the user typed
    sent_url = str(respx.calls[-1].request.url)
    assert "%ED%9D%B0%EC%83%89" in sent_url or "흰색" in sent_url  # 흰색 url-encoded
