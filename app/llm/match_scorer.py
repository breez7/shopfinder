"""LLM match-score + matched-reason for result cards (issues #18 + #19).

Batches results in groups of 10 to amortize the LLM round-trip. One call
produces both the score and the one-line reason; both are merged back into
the SearchResult objects.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Optional

from app.adapters.types import ParsedConditions, SearchResult
from app.llm.client import build_client, get_llm_config

MAX_REASON_CHARS = 40
DEFAULT_BATCH_SIZE = 10

_SYSTEM_PROMPT = """\
You evaluate shopping search results against a user's parsed conditions.
Return STRICT JSON: an array of objects, one per item, in input order.

Each object MUST have:
- "index": integer (echo back the item index)
- "score": integer 0-100 (조건 매칭 정도)
- "reason": Korean string, ≤40 chars, concrete (cite a matched dimension)

Examples of good reasons: "폴리 82%, 루즈핏 표기, 최저가", "검정·100·면 80%",
"가격은 만족, 사이즈 미상". Never repeat the title.
"""

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _extract_json_array(text: str) -> Optional[list]:
    if not text:
        return None
    text = text.strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else None
    except json.JSONDecodeError:
        pass
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
        return parsed if isinstance(parsed, list) else None
    except json.JSONDecodeError:
        return None


def _format_item(idx: int, result: SearchResult) -> str:
    pieces = [f"price={result.price}원" if result.price else "price=?"]
    if result.raw_specs:
        pieces.append(f"specs={result.raw_specs[:60]}")
    pieces.append(f"shop={result.shop_slug}")
    return f"{idx}. title={result.title[:80]} | " + " | ".join(pieces)


async def score_batch(
    conditions: ParsedConditions,
    results: list[SearchResult],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[SearchResult]:
    """Annotate `results` with match_score and matched_reason in place-style
    semantics (returns the same list with each item updated).

    Skipped silently when LLM is unconfigured — callers should not treat that
    as failure; the cards just render without score/reason.
    """
    if not results:
        return results
    if os.getenv("LLM_DISABLE_SCORE", "0") in ("1", "true", "yes"):
        return results

    client = build_client()
    if client is None:
        return results

    _, _, model = get_llm_config()
    if not model:
        return results

    cond_json = conditions.model_dump_json()

    async def _score_one_batch(batch: list[SearchResult], offset: int) -> None:
        if not batch:
            return
        item_lines = "\n".join(_format_item(offset + i, r) for i, r in enumerate(batch))
        user = f"Conditions: {cond_json}\n\nItems:\n{item_lines}\n\nJSON array (one entry per item):"
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.0,
                    max_tokens=120 * len(batch) + 2000,
                ),
                timeout=45.0,
            )
            message = response.choices[0].message
            raw = message.content or ""
            if not raw.strip():
                raw = getattr(message, "reasoning_content", "") or ""
        except Exception:  # noqa: BLE001
            return

        parsed = _extract_json_array(raw)
        if not parsed:
            return

        by_index: dict[int, dict] = {}
        for entry in parsed:
            if isinstance(entry, dict) and isinstance(entry.get("index"), int):
                by_index[entry["index"]] = entry

        for i, r in enumerate(batch):
            entry = by_index.get(offset + i)
            if not entry:
                continue
            score = entry.get("score")
            if isinstance(score, (int, float)):
                r.match_score = max(0.0, min(100.0, float(score)))
            reason = entry.get("reason")
            if isinstance(reason, str):
                r.matched_reason = reason.strip()[:MAX_REASON_CHARS] or None

    # Batch sequentially per-shop concurrency, but simple sequential is fine
    # given Pi 4 constraints and LM Studio's single-stream nature.
    for offset in range(0, len(results), batch_size):
        await _score_one_batch(results[offset : offset + batch_size], offset)

    return results
