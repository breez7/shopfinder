from __future__ import annotations

from pathlib import Path

CSS_PATH = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "web"
    / "static"
    / "css"
    / "app.css"
)


def _css() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


def test_responsive_breakpoints_present() -> None:
    body = _css()
    # Mobile (≤639) collapses to 1 column
    assert "(max-width: 639px)" in body or "(max-width: 640px)" in body
    # Tablet (640~1023) has a 2-column rule
    assert "(min-width: 640px) and (max-width: 1023px)" in body
    # Mobile-only sticky search form
    assert "position: sticky" in body


def test_tap_targets_at_least_44px_on_mobile() -> None:
    body = _css()
    # Critical action buttons get min-height: 44px under the mobile media query
    assert "min-height: 44px" in body
    # Inputs at 16px to prevent iOS zoom on focus
    assert "font-size: 16px" in body


def test_search_form_has_max_width_on_desktop() -> None:
    body = _css()
    # main container is constrained
    assert "max-width: 1100px" in body
