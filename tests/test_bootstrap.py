from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def test_healthz() -> None:
    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_index_renders_with_htmx() -> None:
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        body = response.text
        assert "htmx.org" in body
        assert "ShopFinder" in body


def test_db_file_is_created_on_first_boot(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "test.db"
    monkeypatch.setenv("SHOPFINDER_DB_PATH", str(target))
    get_settings.cache_clear()

    from importlib import reload

    from app import db
    from app.db import session as session_mod

    reload(session_mod)
    reload(db)

    session_mod.init_db()
    assert target.exists()
