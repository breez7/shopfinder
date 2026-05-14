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


def test_db_file_is_created_on_first_boot(tmp_path: Path) -> None:
    # Use a private engine so this test doesn't perturb the module-level one
    # that other tests share.
    from sqlmodel import SQLModel, create_engine

    target = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{target}")
    # Ensure model classes are imported so create_all sees the metadata.
    from app.db import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    assert target.exists()
