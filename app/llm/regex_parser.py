from __future__ import annotations

import re
from typing import Optional

from app.adapters.types import ParsedConditions

# Color dictionary: surface form -> canonical Korean color
COLORS: dict[str, str] = {
    "검정색": "검정", "검정": "검정", "블랙": "검정", "black": "검정",
    "흰색": "흰색", "화이트": "흰색", "white": "흰색", "하얀": "흰색",
    "회색": "회색", "그레이": "회색", "gray": "회색", "grey": "회색",
    "빨강": "빨강", "빨간색": "빨강", "레드": "빨강", "red": "빨강",
    "파랑": "파랑", "파란색": "파랑", "블루": "파랑", "blue": "파랑",
    "네이비": "네이비", "navy": "네이비",
    "초록": "초록", "녹색": "초록", "그린": "초록", "green": "초록",
    "노랑": "노랑", "노란색": "노랑", "옐로우": "노랑", "yellow": "노랑",
    "분홍": "분홍", "핑크": "분홍", "pink": "분홍",
    "보라": "보라", "퍼플": "보라", "purple": "보라",
    "주황": "주황", "오렌지": "주황", "orange": "주황",
    "갈색": "갈색", "브라운": "갈색", "brown": "갈색",
    "베이지": "베이지", "beige": "베이지",
    "카키": "카키", "khaki": "카키",
    "남색": "남색",
}

# Fit terms: surface -> canonical
FITS: dict[str, str] = {
    "루즈핏": "루즈핏", "루즈 핏": "루즈핏", "loose": "루즈핏", "loose fit": "루즈핏",
    "슬림핏": "슬림핏", "슬림 핏": "슬림핏", "slim": "슬림핏", "slim fit": "슬림핏",
    "오버핏": "오버핏", "오버 핏": "오버핏", "over fit": "오버핏", "오버사이즈": "오버핏",
    "레귤러핏": "레귤러핏", "레귤러 핏": "레귤러핏", "regular fit": "레귤러핏", "regular": "레귤러핏",
    "스탠다드핏": "레귤러핏",
    "세미오버핏": "세미오버핏", "세미 오버 핏": "세미오버핏",
}

# Materials dictionary
MATERIALS: list[str] = [
    "폴리에스테르", "폴리에스터", "폴리",
    "면", "코튼", "cotton",
    "린넨", "리넨", "linen",
    "울", "wool", "양모",
    "캐시미어", "cashmere",
    "나일론", "nylon",
    "스판", "스판덱스", "spandex", "lycra",
    "레이온", "rayon",
    "실크", "silk",
    "데님", "denim",
    "가죽", "leather",
]

# Common clothing categories
CATEGORIES: list[str] = [
    "남방", "셔츠", "와이셔츠", "블라우스",
    "티셔츠", "티", "맨투맨", "스웻셔츠",
    "후드", "후드티", "후드집업", "집업",
    "니트", "스웨터", "가디건", "카디건",
    "바지", "팬츠", "청바지", "데님", "슬랙스", "치노",
    "반바지", "쇼츠",
    "재킷", "자켓", "코트", "패딩", "점퍼",
    "원피스", "치마", "스커트",
    "양말", "신발", "스니커즈", "운동화", "구두",
    "모자", "캡", "비니",
]

# Sleeves
SLEEVES = ["긴팔", "반팔", "민소매", "7부", "5부", "긴소매", "반소매"]


def _parse_max_price(text: str) -> Optional[int]:
    """Match patterns like '2만원 이하', '20000원 이하', '20,000원 이하', 'under 20000'."""
    # 만/천 in Korean: '2만원', '1만5천원', '15만원'
    m = re.search(
        r"(\d+)\s*만(?:\s*(\d+)\s*천)?\s*원?\s*(?:이하|미만|아래|under)?",
        text,
    )
    if m:
        man = int(m.group(1)) * 10000
        cheon = int(m.group(2)) * 1000 if m.group(2) else 0
        # Only treat as price if followed by 이하/미만/아래/under or '원'
        snippet = m.group(0)
        if "원" in snippet or "이하" in snippet or "미만" in snippet or "아래" in snippet or "under" in snippet.lower():
            return man + cheon

    # Numeric with optional commas and 원/won + 이하/under
    m = re.search(
        r"(?:under\s+)?(\d{1,3}(?:,\d{3})+|\d{4,7})\s*원?\s*(?:이하|미만|아래)?",
        text,
        re.IGNORECASE,
    )
    if m:
        snippet = m.group(0).lower()
        if "이하" in snippet or "미만" in snippet or "아래" in snippet or "under" in snippet or "원" in snippet:
            num = int(m.group(1).replace(",", ""))
            return num

    return None


def _parse_size(text: str) -> Optional[str]:
    """Match numeric sizes (85-130 typical for Korean) or letter sizes."""
    # Letter sizes — be careful not to grab loose 's'/'m'/'l' inside other words
    m = re.search(r"\b(XXL|XL|XS|S|M|L)\b", text)
    if m:
        return m.group(1).upper()
    # Numeric sizes — explicit '사이즈' context wins
    m = re.search(r"(\d{2,3})\s*사이즈", text)
    if m:
        return m.group(1)
    # Standalone 85-130
    for m in re.finditer(r"\b(\d{2,3})\b", text):
        n = int(m.group(1))
        if 85 <= n <= 130:
            return str(n)
    return None


def _parse_material(text: str) -> tuple[Optional[str], Optional[int]]:
    """Find material and optional percentage threshold."""
    for mat in sorted(MATERIALS, key=len, reverse=True):
        if mat in text:
            # Look for '폴리에스테르 80', '폴리에스테르 80%', '폴리에스테르 80 이상'
            window = text[text.index(mat) : text.index(mat) + len(mat) + 20]
            m = re.search(r"(\d{1,3})\s*%?\s*(?:이상|넘는|over|초과)?", window[len(mat):])
            pct: Optional[int] = None
            if m:
                val = int(m.group(1))
                if 1 <= val <= 100:
                    pct = val
            return mat, pct
    return None, None


def _parse_color(text: str) -> Optional[str]:
    for surface in sorted(COLORS.keys(), key=len, reverse=True):
        # English colors should be word-boundary matched to avoid false positives
        if surface.isascii():
            if re.search(rf"\b{re.escape(surface)}\b", text, re.IGNORECASE):
                return COLORS[surface]
        else:
            if surface in text:
                return COLORS[surface]
    return None


def _parse_fit(text: str) -> Optional[str]:
    for surface in sorted(FITS.keys(), key=len, reverse=True):
        if surface.isascii():
            if re.search(rf"\b{re.escape(surface)}\b", text, re.IGNORECASE):
                return FITS[surface]
        else:
            if surface in text:
                return FITS[surface]
    return None


def _parse_category(text: str) -> Optional[str]:
    """Find a category, prefixing a sleeve qualifier when present right before."""
    sleeve: Optional[str] = None
    for sl in SLEEVES:
        if sl in text:
            sleeve = sl
            break
    for cat in sorted(CATEGORIES, key=len, reverse=True):
        if cat in text:
            if sleeve and sleeve in text:
                # Prefix sleeve if it appears in the query (e.g., "긴팔 남방")
                return f"{sleeve} {cat}"
            return cat
    return sleeve


def _build_free_text(text: str, consumed: list[str]) -> str:
    """Whatever the parser didn't recognize, keep as free_text for the keyword build."""
    remainder = text
    for token in consumed:
        if not token:
            continue
        remainder = remainder.replace(token, " ")
    # Strip common price/percentage residues
    remainder = re.sub(r"\d+\s*만\s*\d*\s*천?\s*원?\s*이하?", " ", remainder)
    remainder = re.sub(r"\d+\s*%", " ", remainder)
    remainder = re.sub(r"\d+\s*이상", " ", remainder)
    remainder = re.sub(r"\d+\s*사이즈", " ", remainder)
    remainder = re.sub(r"\s+", " ", remainder).strip()
    return remainder


def parse(query: str) -> ParsedConditions:
    """Parse a Korean/English natural-language query into structured conditions.

    Never raises — unknown input falls through to free_text.
    """
    text = query.strip()
    if not text:
        return ParsedConditions()

    color = _parse_color(text)
    fit = _parse_fit(text)
    size = _parse_size(text)
    material, material_pct = _parse_material(text)
    category = _parse_category(text)
    max_price = _parse_max_price(text)

    consumed = [
        color,
        fit,
        size,
        material,
        category,
    ]
    free_text = _build_free_text(text, [c for c in consumed if c])

    return ParsedConditions(
        category=category,
        color=color,
        size=size,
        material=material,
        material_pct=material_pct,
        fit=fit,
        max_price=max_price,
        free_text=free_text,
    )
