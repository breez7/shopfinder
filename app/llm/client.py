"""Thin wrapper that builds an AsyncOpenAI client from the settings table.

Returns None when the LLM is not configured. Used by #16 parser, #17 query
optimizer, #18/#19 scorer, and the #15 settings UI for the connection test.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx
from openai import AsyncOpenAI
from sqlmodel import Session

from app.db.session import engine
from app.settings_store import (
    KEY_LLM_API_KEY,
    KEY_LLM_BASE_URL,
    KEY_LLM_MODEL,
    get as settings_get,
)


def get_llm_config() -> tuple[str, str, str]:
    """Return (base_url, api_key, model). Settings table wins; falls back to
    LLM_BASE_URL / LLM_API_KEY / LLM_MODEL env vars when a slot is empty."""
    with Session(engine) as session:
        base = settings_get(session, KEY_LLM_BASE_URL) or os.getenv("LLM_BASE_URL", "")
        key = settings_get(session, KEY_LLM_API_KEY) or os.getenv("LLM_API_KEY", "")
        model = settings_get(session, KEY_LLM_MODEL) or os.getenv("LLM_MODEL", "")
    return base, key, model


def build_client() -> Optional[AsyncOpenAI]:
    base, key, _ = get_llm_config()
    if not base:
        return None
    return AsyncOpenAI(base_url=base, api_key=key or "sk-no-key", timeout=20)


async def connection_test() -> tuple[bool, str]:
    """Hit {base_url}/models with the API key. Returns (ok, status_message)."""
    base, key, _ = get_llm_config()
    if not base:
        return False, "LLM 베이스 URL이 설정되지 않았습니다"

    url = base.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return False, f"연결 실패: {type(exc).__name__}: {exc}"

    if 200 <= response.status_code < 300:
        try:
            data = response.json()
            count = len(data.get("data", [])) if isinstance(data, dict) else 0
            return True, f"OK (HTTP {response.status_code}, 모델 {count}개)"
        except ValueError:
            return True, f"OK (HTTP {response.status_code})"
    return False, f"HTTP {response.status_code}: {response.text[:160]}"
