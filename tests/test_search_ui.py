from fastapi.testclient import TestClient

from app.main import app


def test_index_has_search_form_and_placeholders() -> None:
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        body = r.text
        # Form posts via JS (search.js) which calls /parse + opens SSE for /search/stream
        assert 'id="search-form"' in body
        assert 'name="q"' in body
        assert 'id="parsed-panel"' in body
        assert 'id="result-grid"' in body
        assert 'id="shop-status"' in body
        # JS file loaded
        assert "/static/js/search.js" in body
        # Viewport meta for mobile
        assert 'name="viewport"' in body


def test_parse_endpoint_returns_chips_for_known_query() -> None:
    with TestClient(app) as client:
        r = client.post("/parse", data={"q": "검정 100 사이즈 남방 2만원 이하"})
        assert r.status_code == 200
        body = r.text
        assert "검정" in body
        assert "100" in body
        assert "남방" in body
        assert "20,000원" in body
        assert "regex" in body


def test_parse_endpoint_empty_query() -> None:
    with TestClient(app) as client:
        r = client.post("/parse", data={"q": ""})
        assert r.status_code == 200
        assert "파싱된 조건이 없습니다" in r.text


def test_parse_endpoint_renders_free_text_chip_for_unknown_input() -> None:
    with TestClient(app) as client:
        r = client.post("/parse", data={"q": "아주 멋진 신상"})
        assert r.status_code == 200
        assert "키워드" in r.text  # free_text chip label
