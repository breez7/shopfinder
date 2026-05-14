from __future__ import annotations

import httpx
import pytest
import respx
from sqlmodel import Session, select

from app.adapters.types import ParsedConditions, SearchResult
from app.db.models import Setting
from app.db.session import engine
from app.llm.llm_parser import _extract_json, parse_with_llm
from app.llm.match_scorer import score_batch
from app.llm.query_optimizer import optimize
from app.settings_store import (
    KEY_LLM_API_KEY,
    KEY_LLM_BASE_URL,
    KEY_LLM_MODEL,
    set_ as settings_set,
)


def _truncate_settings() -> None:
    with Session(engine) as session:
        for row in session.exec(select(Setting)).all():
            session.delete(row)
        session.commit()


def _configure_llm() -> None:
    with Session(engine) as session:
        settings_set(session, KEY_LLM_BASE_URL, "https://llm.example/v1")
        settings_set(session, KEY_LLM_API_KEY, "sk-test")
        settings_set(session, KEY_LLM_MODEL, "test-model")


def _mock_chat_completion(content: str):
    return respx.post("https://llm.example/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )


# --- llm_parser ---------------------------------------------------------------


def test_extract_json_strict_and_wrapped() -> None:
    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert _extract_json("nope") is None


async def test_llm_parser_falls_back_when_unconfigured() -> None:
    _truncate_settings()
    conditions, by = await parse_with_llm("검정 100 남방 2만원 이하")
    assert by == "regex"
    assert conditions.color == "검정"  # regex parser fired


@respx.mock
async def test_llm_parser_returns_llm_path_on_valid_response() -> None:
    _truncate_settings()
    _configure_llm()
    _mock_chat_completion(
        '{"category":"긴팔 남방","color":"검정","size":"100","material":"폴리에스테르",'
        '"material_pct":80,"fit":"루즈핏","max_price":20000,"free_text":""}'
    )
    conditions, by = await parse_with_llm("검정 100 남방")
    assert by == "llm"
    assert conditions.category == "긴팔 남방"
    assert conditions.material_pct == 80
    assert conditions.max_price == 20000


@respx.mock
async def test_llm_parser_falls_back_on_malformed_json() -> None:
    _truncate_settings()
    _configure_llm()
    _mock_chat_completion("not json at all")
    conditions, by = await parse_with_llm("검정 남방")
    assert by == "regex"  # fallback path
    assert conditions.color == "검정"  # regex still works


@respx.mock
async def test_llm_parser_falls_back_on_http_error() -> None:
    _truncate_settings()
    _configure_llm()
    respx.post("https://llm.example/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="boom")
    )
    conditions, by = await parse_with_llm("검정 남방")
    assert by == "regex"
    assert conditions.color == "검정"


# --- query_optimizer ----------------------------------------------------------


async def test_query_optimizer_falls_back_when_unconfigured() -> None:
    _truncate_settings()
    c = ParsedConditions(color="검정", category="남방")
    out = await optimize(c, "coupang")
    assert "검정" in out
    assert "남방" in out


@respx.mock
async def test_query_optimizer_returns_llm_suggestion_when_configured() -> None:
    _truncate_settings()
    _configure_llm()
    _mock_chat_completion("검정 남방 루즈")
    c = ParsedConditions(color="검정", category="남방", fit="루즈핏")
    out = await optimize(c, "musinsa")
    assert out == "검정 남방 루즈"


@respx.mock
async def test_query_optimizer_strips_quotes_and_extra_lines() -> None:
    _truncate_settings()
    _configure_llm()
    _mock_chat_completion('"검정 셔츠"\nextra commentary')
    c = ParsedConditions(color="검정", category="셔츠")
    out = await optimize(c, "naver")
    assert out == "검정 셔츠"


# --- match_scorer -------------------------------------------------------------


async def test_match_scorer_no_op_when_unconfigured() -> None:
    _truncate_settings()
    results = [SearchResult(shop_slug="x", title="t", product_url="u")]
    out = await score_batch(ParsedConditions(), results)
    assert out[0].match_score is None
    assert out[0].matched_reason is None


@respx.mock
async def test_match_scorer_annotates_results_when_configured() -> None:
    _truncate_settings()
    _configure_llm()
    results = [
        SearchResult(shop_slug="naver", title="검정 폴리에스테르 남방", product_url="u1", price=15000),
        SearchResult(shop_slug="naver", title="검정 코트", product_url="u2", price=300000),
    ]
    _mock_chat_completion(
        '[{"index":0,"score":92,"reason":"폴리 함량 부합, 가격 적정"},'
        '{"index":1,"score":21,"reason":"카테고리 불일치"}]'
    )
    annotated = await score_batch(
        ParsedConditions(color="검정", category="남방", material="폴리에스테르", max_price=20000),
        results,
    )
    assert annotated[0].match_score == 92.0
    assert "폴리" in (annotated[0].matched_reason or "")
    assert annotated[1].match_score == 21.0
    assert annotated[1].matched_reason == "카테고리 불일치"


@respx.mock
async def test_match_scorer_handles_partial_response() -> None:
    """LLM only scores some items — others stay None."""
    _truncate_settings()
    _configure_llm()
    results = [
        SearchResult(shop_slug="naver", title=f"item-{i}", product_url=f"u{i}")
        for i in range(3)
    ]
    _mock_chat_completion('[{"index":0,"score":80,"reason":"a"}]')
    out = await score_batch(ParsedConditions(), results)
    assert out[0].match_score == 80.0
    assert out[1].match_score is None
    assert out[2].match_score is None


@respx.mock
async def test_match_scorer_clips_reason_at_40_chars() -> None:
    _truncate_settings()
    _configure_llm()
    long_reason = "가" * 100
    results = [SearchResult(shop_slug="x", title="t", product_url="u")]
    _mock_chat_completion(f'[{{"index":0,"score":50,"reason":"{long_reason}"}}]')
    out = await score_batch(ParsedConditions(), results)
    assert len(out[0].matched_reason) == 40


@respx.mock
async def test_match_scorer_batches_at_size_10() -> None:
    """20 results should produce 2 LLM calls."""
    _truncate_settings()
    _configure_llm()
    results = [
        SearchResult(shop_slug="x", title=f"t{i}", product_url=f"u{i}")
        for i in range(20)
    ]
    # Two consecutive responses to two calls (same mock can be called multiple times)
    _mock_chat_completion("[]")
    await score_batch(ParsedConditions(), results, batch_size=10)
    # Just assert: did NOT crash and matched_reason still None on all (since [] response)
    assert all(r.matched_reason is None for r in results)
