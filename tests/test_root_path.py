"""Verify root_path support (running behind Traefik StripPrefix).

The default test app uses root_path=''; this file builds a fresh app with
root_path='/shopfinder' set via env and confirms templates emit the prefix.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_default_root_path_emits_no_prefix() -> None:
    """Default app (root_path empty) emits root-relative links unchanged."""
    from app.main import app

    with TestClient(app) as client:
        body = client.get("/").text
        # No '/shopfinder' anywhere
        assert "/shopfinder" not in body
        # And SHOPFINDER_BASE is the empty string
        assert 'window.SHOPFINDER_BASE = ""' in body


def test_root_path_prefixes_nav_and_form_actions(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("SHOPFINDER_ROOT_PATH", "/shopfinder")
    monkeypatch.setenv("SHOPFINDER_DB_PATH", str(tmp_path / "test.db"))

    # Force a fresh Settings and app
    from app.config import get_settings

    get_settings.cache_clear()
    # Reload main so the templates pick up new base_path
    import importlib

    from app import main as main_module
    importlib.reload(main_module)
    fresh_app = main_module.create_app()

    with TestClient(fresh_app) as client:
        body = client.get("/").text
        # Nav links prefixed
        assert 'href="/shopfinder/"' in body
        assert 'href="/shopfinder/history"' in body
        assert 'href="/shopfinder/settings"' in body
        # JS base path injected
        assert 'window.SHOPFINDER_BASE = "/shopfinder"' in body
        # Static asset URL also prefixed by FastAPI's url_for
        assert "/shopfinder/static/css/app.css" in body

    # Cleanup: reload main one more time with the env unset so subsequent
    # tests in the suite see the default app.
    monkeypatch.delenv("SHOPFINDER_ROOT_PATH", raising=False)
    monkeypatch.delenv("SHOPFINDER_DB_PATH", raising=False)
    get_settings.cache_clear()
    importlib.reload(main_module)
