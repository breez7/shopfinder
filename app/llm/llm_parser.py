"""LLM-based natural-language condition parser (issue #16).

Falls back to the regex parser on any failure (timeout, malformed JSON,
validation error). Always returns a valid ParsedConditions.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from app.adapters.types import ParsedConditions
from app.llm.client import build_client, get_llm_config
from app.llm.regex_parser import parse as regex_parse

_SYSTEM_PROMPT = """\
You extract structured shopping conditions from a Korean (or English) natural-language query.
Return STRICT JSON only — no commentary, no markdown fences.

Schema:
{
  "category": string | null,        // 의류 카테고리 (남방, 셔츠, 티셔츠, 후드, 바지, ...)
  "color": string | null,           // 한국어 색상 (검정, 흰색, 회색, 네이비, 빨강, ...)
  "size": string | null,            // 사이즈 (예: "100", "XL")
  "material": string | null,        // 소재 (예: "폴리에스테르", "면", "린넨")
  "material_pct": integer | null,   // 1-100 정수, 소재 함량 임계값
  "fit": string | null,             // 핏 (루즈핏 / 슬림핏 / 오버핏 / 레귤러핏)
  "max_price": integer | null,      // 최대 가격(원) 정수
  "free_text": string               // 위 필드로 분류되지 않은 키워드 (없으면 빈 문자열)
}

Examples:

Query: "검정색 100 사이즈 긴팔 남방 폴리에스테르 80 이상 루즈핏 2만원 이하"
JSON: {"category":"긴팔 남방","color":"검정","size":"100","material":"폴리에스테르","material_pct":80,"fit":"루즈핏","max_price":20000,"free_text":""}

Query: "가성비 좋은 폴리 함량 높은 검정 남방"
JSON: {"category":"남방","color":"검정","size":null,"material":"폴리에스테르","material_pct":null,"fit":null,"max_price":null,"free_text":"가성비"}

Query: "white linen shirt under 50000"
JSON: {"category":"셔츠","color":"흰색","size":null,"material":"린넨","material_pct":null,"fit":null,"max_price":50000,"free_text":""}
"""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> Optional[dict]:
    """Pull a JSON object out of the response — strict or wrapped in fences."""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def parse_with_llm(query: str) -> tuple[ParsedConditions, str]:
    """Parse a query using the configured LLM. Returns (conditions, parsed_by).

    `parsed_by` is "llm" on success, "regex" when the LLM is unconfigured or
    any error path is hit. Never raises.
    """
    if not query.strip():
        return regex_parse(query), "regex"

    client = build_client()
    if client is None:
        return regex_parse(query), "regex"

    _, _, model = get_llm_config()
    if not model:
        return regex_parse(query), "regex"

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Query: {query}\nJSON:"},
            ],
            temperature=0.0,
            max_tokens=2000,
        )
    except Exception:  # noqa: BLE001
        return regex_parse(query), "regex"

    raw = ""
    try:
        message = response.choices[0].message
        raw = message.content or ""
        # Reasoning models (e.g. GLM-4.5/5) may put the JSON in
        # reasoning_content when content ends up empty.
        if not raw.strip():
            raw = getattr(message, "reasoning_content", "") or ""
    except (AttributeError, IndexError):
        pass

    data = _extract_json(raw)
    if not isinstance(data, dict):
        return regex_parse(query), "regex"

    try:
        conditions = ParsedConditions(**{k: data.get(k) for k in ParsedConditions.model_fields})
    except Exception:  # noqa: BLE001 — pydantic ValidationError + any sub-type weirdness
        return regex_parse(query), "regex"

    return conditions, "llm"
