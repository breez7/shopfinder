from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import Setting
from app.db.session import engine
from app.main import app
from app.settings_store import (
    KEY_LLM_API_KEY,
    KEY_LLM_BASE_URL,
    KEY_LLM_MODEL,
    KEY_NAVER_CLIENT_SECRET,
    get as settings_get,
    mask,
    set_ as settings_set,
)


def _truncate_settings() -> None:
    with Session(engine) as session:
        for row in session.exec(select(Setting)).all():
            session.delete(row)
        session.commit()


def test_settings_store_round_trip() -> None:
    _truncate_settings()
    with Session(engine) as session:
        assert settings_get(session, "missing", "default") == "default"
        settings_set(session, "k", "v")
        assert settings_get(session, "k") == "v"
        settings_set(session, "k", "v2")  # update
        assert settings_get(session, "k") == "v2"


def test_mask_helper() -> None:
    assert mask("") == ""
    assert mask("abc") == "***"
    assert mask("sk-12345678") == "sk-1…78"


def test_settings_page_renders_form() -> None:
    _truncate_settings()
    with Session(engine) as session:
        settings_set(session, KEY_LLM_BASE_URL, "http://lan:1234/v1")
        settings_set(session, KEY_LLM_MODEL, "glm-4-flash")
        settings_set(session, KEY_LLM_API_KEY, "sk-abcdef0123456789")
        settings_set(session, KEY_NAVER_CLIENT_SECRET, "secret-xyz")

    with TestClient(app) as client:
        r = client.get("/settings")
        assert r.status_code == 200
        body = r.text
        assert "http://lan:1234/v1" in body
        assert "glm-4-flash" in body
        # API key never echoed in cleartext, only masked
        assert "sk-abcdef0123456789" not in body
        assert mask("sk-abcdef0123456789") in body
        # Naver secret similarly masked
        assert "secret-xyz" not in body


def test_settings_save_does_not_clear_existing_key_when_input_blank() -> None:
    _truncate_settings()
    with Session(engine) as session:
        settings_set(session, KEY_LLM_API_KEY, "existing-key")
        settings_set(session, KEY_NAVER_CLIENT_SECRET, "existing-secret")

    with TestClient(app) as client:
        r = client.post(
            "/settings",
            data={
                "llm_base_url": "http://x",
                "llm_api_key": "",  # blank => keep existing
                "llm_model": "m",
                "llm_call_cap": "5",
                "naver_client_id": "id",
                "naver_client_secret": "",  # blank => keep existing
            },
        )
        assert r.status_code == 200
        assert "저장됨" in r.text

    with Session(engine) as session:
        assert settings_get(session, KEY_LLM_API_KEY) == "existing-key"
        assert settings_get(session, KEY_NAVER_CLIENT_SECRET) == "existing-secret"
        assert settings_get(session, KEY_LLM_BASE_URL) == "http://x"
        assert settings_get(session, KEY_LLM_MODEL) == "m"


def test_settings_save_updates_key_when_input_present() -> None:
    _truncate_settings()
    with TestClient(app) as client:
        r = client.post(
            "/settings",
            data={
                "llm_base_url": "http://x",
                "llm_api_key": "new-key",
                "llm_model": "",
                "llm_call_cap": "0",
                "naver_client_id": "",
                "naver_client_secret": "new-secret",
            },
        )
        assert r.status_code == 200

    with Session(engine) as session:
        assert settings_get(session, KEY_LLM_API_KEY) == "new-key"
        assert settings_get(session, KEY_NAVER_CLIENT_SECRET) == "new-secret"


@respx.mock
async def test_llm_connection_test_succeeds_on_200() -> None:
    _truncate_settings()
    with Session(engine) as session:
        settings_set(session, KEY_LLM_BASE_URL, "https://api.example.com/v1")
        settings_set(session, KEY_LLM_API_KEY, "sk-test")

    respx.get("https://api.example.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]})
    )

    with TestClient(app) as client:
        r = client.post("/settings/llm-test")
        assert r.status_code == 200
        assert "OK" in r.text
        assert "2개" in r.text


@respx.mock
async def test_llm_connection_test_reports_failure() -> None:
    _truncate_settings()
    with Session(engine) as session:
        settings_set(session, KEY_LLM_BASE_URL, "https://api.example.com/v1")

    respx.get("https://api.example.com/v1/models").mock(
        return_value=httpx.Response(401, text="bad key")
    )

    with TestClient(app) as client:
        r = client.post("/settings/llm-test")
        assert r.status_code == 200
        assert "401" in r.text


def test_llm_connection_test_when_unconfigured() -> None:
    _truncate_settings()
    with TestClient(app) as client:
        r = client.post("/settings/llm-test")
        assert r.status_code == 200
        assert "설정되지 않" in r.text
