"""Strict client-side filter that drops shop results whose title/specs
don't actually contain the parsed conditions.

Korean shopping sites often return loose matches (e.g. searching '붉은색
스트라이프 셔츠' surfaces non-red and non-striped items because the site
indexes free-text tokens broadly). For the self-hoster's "엄격하게 지켜줘"
requirement we drop anything whose visible text doesn't contain a token
matching each non-empty condition.

We intentionally do NOT touch raw_specs alone — many sites only put
brand/seller there, with the descriptive tokens living in `title`.
"""
from __future__ import annotations

from app.adapters.types import ParsedConditions, SearchResult
from app.llm.regex_parser import COLORS, FITS, MATERIALS


def _build_synonym_groups(surface_to_canonical: dict[str, str]) -> dict[str, list[str]]:
    """Invert a 'surface form -> canonical' map into 'canonical -> all surface
    forms (including the canonical itself)'."""
    out: dict[str, set[str]] = {}
    for surface, canonical in surface_to_canonical.items():
        out.setdefault(canonical, set()).add(surface)
    for canonical in list(out.keys()):
        out[canonical].add(canonical)
    return {k: sorted(v, key=len, reverse=True) for k, v in out.items()}


_COLOR_GROUPS = _build_synonym_groups(COLORS)
_FIT_GROUPS = _build_synonym_groups(FITS)


def _matches_any(text: str, candidates: list[str]) -> bool:
    text_lower = text.lower()
    for c in candidates:
        if c and c.lower() in text_lower:
            return True
    return False


def matches_conditions(result: SearchResult, conditions: ParsedConditions) -> bool:
    """Return False when the result clearly violates a non-null condition.

    The function is permissive on dimensions that the title rarely
    advertises (`material_pct`, very specific size numbers) but strict on
    color, category, fit, material, and free-text keywords."""
    text = f"{result.title} {result.raw_specs}".lower()

    # Color — must contain at least one surface form of the canonical color.
    if conditions.color:
        surfaces = _COLOR_GROUPS.get(conditions.color, [conditions.color])
        if not _matches_any(text, surfaces):
            return False

    # Category — accept any token of the parsed category (handles "긴팔 남방"
    # where we want either word, not the full phrase). Free_text below
    # handles design keywords like "스트라이프" / "체크".
    if conditions.category:
        parts = [p for p in conditions.category.split() if len(p) >= 1]
        if parts and not any(p.lower() in text for p in parts):
            return False

    # Fit — same synonym treatment as color.
    if conditions.fit:
        surfaces = _FIT_GROUPS.get(conditions.fit, [conditions.fit])
        if not _matches_any(text, surfaces):
            return False

    # Material — direct substring (the regex parser already canonicalized).
    if conditions.material:
        if conditions.material.lower() not in text:
            return False

    # Free text — every >=2-char token in the residue must appear.
    for token in (conditions.free_text or "").split():
        token = token.strip()
        if len(token) < 2:
            continue
        if token.lower() not in text:
            return False

    # max_price is already enforced inside each adapter; we don't re-check
    # because some adapters surface no price at all (sale-only badges,
    # etc.) and dropping them would hide otherwise valid hits.
    return True
