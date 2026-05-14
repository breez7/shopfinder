"""Shop-specific search-query optimizer (issue #17).

Given parsed conditions + a shop slug, produce a keyword string that should
maximize recall on that shop's search UX. Returns the naive
`conditions.keyword()` when no LLM is configured.
"""
from __future__ import annotations

import asyncio
import os

from app.adapters.types import ParsedConditions
from app.llm.client import build_client, get_llm_config

# Per-shop one-liners describing the site's idiom. Used in the prompt so the
# LLM can match the tokenization style each shop expects.
SHOP_HINTS: dict[str, str] = {
    "naver": "광범위한 마켓플레이스 - 자연어 그대로도 잘 검색",
    "coupang": "긴 자연어보다 짧은 명사구 키워드를 선호",
    "eleventh": "짧은 명사구 + 색상/사이즈 토큰을 분리",
    "gmarket": "긴 자연어보다 짧은 키워드 + 카테고리 명사",
    "musinsa": "패션 특화 - 핏/소재/스타일 토큰을 키워드에 포함",
}

_SYSTEM_PROMPT = """\
You compress structured shopping conditions into a single short keyword string
optimized for one Korean shopping site's search box. Output ONLY the keyword
string — no quotes, no explanation, no markdown.
"""


async def optimize(conditions: ParsedConditions, shop_slug: str) -> str:
    """Return a shop-specific keyword string. Falls back to conditions.keyword().

    Disabled entirely when LLM_DISABLE_OPTIMIZE=1 — useful on slow/expensive
    endpoints where the per-shop optimization isn't worth the latency cost.
    """
    naive = conditions.keyword()
    if os.getenv("LLM_DISABLE_OPTIMIZE", "0") in ("1", "true", "yes"):
        return naive
    client = build_client()
    if client is None:
        return naive

    _, _, model = get_llm_config()
    if not model:
        return naive

    hint = SHOP_HINTS.get(shop_slug, "")
    user = (
        f"Shop: {shop_slug}\n"
        f"Hint: {hint}\n"
        f"Conditions (JSON): {conditions.model_dump_json()}\n"
        f"Naive keyword: {naive}\n"
        "Optimized keyword (one line, plain text):"
    )

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
                max_tokens=600,
            ),
            timeout=15.0,
        )
        message = response.choices[0].message
        raw = (message.content or "").strip()
        if not raw:
            raw = (getattr(message, "reasoning_content", "") or "").strip()
        if not raw:
            return naive
        first_line = raw.splitlines()[-1].strip()
        return first_line.strip("\"'") or naive
    except Exception:  # noqa: BLE001
        return naive
