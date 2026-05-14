from app.adapters.types import ParsedConditions, SearchResult
from app.result_filter import matches_conditions


def _r(title: str, specs: str = "") -> SearchResult:
    return SearchResult(shop_slug="x", title=title, product_url="u", raw_specs=specs)


def test_passes_when_no_conditions_set() -> None:
    assert matches_conditions(_r("아무 셔츠"), ParsedConditions())


def test_drops_wrong_color() -> None:
    c = ParsedConditions(color="빨강")
    assert not matches_conditions(_r("검정 셔츠"), c)
    assert matches_conditions(_r("빨간 셔츠"), c)
    assert matches_conditions(_r("붉은색 셔츠"), c)
    assert matches_conditions(_r("RED stripe shirt"), c)


def test_color_synonyms_for_black() -> None:
    c = ParsedConditions(color="검정")
    assert matches_conditions(_r("검정 셔츠"), c)
    assert matches_conditions(_r("블랙 셔츠"), c)
    assert matches_conditions(_r("Black T-shirt"), c)
    assert not matches_conditions(_r("흰색 셔츠"), c)


def test_drops_wrong_category() -> None:
    c = ParsedConditions(category="셔츠")
    assert matches_conditions(_r("긴팔 셔츠"), c)
    assert not matches_conditions(_r("청바지 데님"), c)


def test_drops_wrong_fit() -> None:
    c = ParsedConditions(fit="루즈핏")
    assert matches_conditions(_r("루즈핏 데일리 셔츠"), c)
    assert not matches_conditions(_r("슬림핏 셔츠"), c)


def test_drops_wrong_material() -> None:
    c = ParsedConditions(material="린넨")
    assert matches_conditions(_r("린넨 100% 셔츠"), c)
    assert not matches_conditions(_r("폴리에스테르 셔츠"), c)


def test_strict_free_text() -> None:
    """User example: "붉은색 스트라이프 셔츠" — 스트라이프 lives in free_text."""
    c = ParsedConditions(color="빨강", category="셔츠", free_text="스트라이프")
    assert matches_conditions(_r("붉은색 스트라이프 셔츠"), c)
    assert matches_conditions(_r("빨강 스트라이프 셔츠"), c)
    # Wrong pattern -> dropped
    assert not matches_conditions(_r("빨강 체크 셔츠"), c)
    # Wrong color -> dropped
    assert not matches_conditions(_r("검정 스트라이프 셔츠"), c)
    # Wrong category -> dropped
    assert not matches_conditions(_r("빨강 스트라이프 바지"), c)


def test_searches_in_raw_specs_too() -> None:
    c = ParsedConditions(color="검정")
    assert matches_conditions(_r("셔츠", specs="브랜드A 검정 단일 색상"), c)


def test_short_free_text_tokens_ignored() -> None:
    """Free-text tokens shorter than 2 chars don't trigger drops."""
    c = ParsedConditions(free_text="a b 셔츠")
    assert matches_conditions(_r("아무 셔츠 신상"), c)
