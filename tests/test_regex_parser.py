from __future__ import annotations

import pytest

from app.llm.regex_parser import parse


def test_prd_example_query() -> None:
    """PRD §6 example query — must parse all six dimensions correctly."""
    q = "검정색 100 사이즈 긴팔 남방 폴리에스테르 80 이상 루즈핏 2만원 이하"
    result = parse(q)
    assert result.color == "검정"
    assert result.size == "100"
    assert result.material == "폴리에스테르"
    assert result.material_pct == 80
    assert result.fit == "루즈핏"
    assert result.max_price == 20000
    assert "남방" in (result.category or "")
    assert "긴팔" in (result.category or "")


@pytest.mark.parametrize(
    "query,expected_color",
    [
        ("검정 티셔츠", "검정"),
        ("블랙 후드", "검정"),
        ("black t-shirt", "검정"),
        ("화이트 셔츠", "흰색"),
        ("네이비 슬랙스", "네이비"),
        ("그레이 맨투맨", "회색"),
    ],
)
def test_color_variants(query: str, expected_color: str) -> None:
    assert parse(query).color == expected_color


@pytest.mark.parametrize(
    "query,expected_price",
    [
        ("2만원 이하", 20000),
        ("20000원 이하", 20000),
        ("20,000원 이하", 20000),
        ("under 50000", 50000),
        ("1만5천원 이하", 15000),
        ("15만원 이하", 150000),
    ],
)
def test_price_variants(query: str, expected_price: int) -> None:
    assert parse(query).max_price == expected_price


@pytest.mark.parametrize(
    "query,expected_size",
    [
        ("100 사이즈", "100"),
        ("XL 사이즈", "XL"),
        ("M", "M"),
        ("110 셔츠", "110"),
    ],
)
def test_size_variants(query: str, expected_size: str) -> None:
    assert parse(query).size == expected_size


@pytest.mark.parametrize(
    "query,expected_fit",
    [
        ("루즈핏 셔츠", "루즈핏"),
        ("loose fit shirt", "루즈핏"),
        ("오버핏 후드", "오버핏"),
        ("slim 슬랙스", "슬림핏"),
    ],
)
def test_fit_variants(query: str, expected_fit: str) -> None:
    assert parse(query).fit == expected_fit


def test_material_with_percentage() -> None:
    r = parse("면 100% 셔츠")
    assert r.material == "면"
    assert r.material_pct == 100


def test_material_without_percentage() -> None:
    r = parse("린넨 셔츠")
    assert r.material == "린넨"
    assert r.material_pct is None


def test_empty_query_returns_empty_conditions() -> None:
    r = parse("")
    assert r.color is None
    assert r.size is None
    assert r.free_text == ""


def test_unknown_input_falls_through_to_free_text() -> None:
    r = parse("아주 멋진 무언가")
    assert r.color is None
    assert r.category is None
    assert "멋진" in r.free_text


def test_never_raises_on_weird_input() -> None:
    # Whatever we throw at it, it must not raise
    parse("!!!@@@$$$$$%%%")
    parse("12345")
    parse("a" * 5000)


def test_sleeve_is_prefixed_to_category() -> None:
    r = parse("반팔 티셔츠")
    assert r.category is not None
    assert "반팔" in r.category
    assert "티" in r.category


def test_consumed_tokens_not_in_free_text() -> None:
    r = parse("검정 100 사이즈 남방 2만원 이하")
    # The parsed dimensions shouldn't reappear in free_text
    assert "검정" not in r.free_text
    assert "남방" not in r.free_text
    assert "100" not in r.free_text
