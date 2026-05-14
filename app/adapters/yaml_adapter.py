"""User-defined adapters described declaratively by YAML/JSON (issue #24).

Each shops row whose adapter_module points at this class carries a
config_json string with: search_url_template + the standard selector set.
"""
from __future__ import annotations

from app.adapters.html_base import HtmlSearchAdapter

REQUIRED_KEYS = (
    "search_url_template",
    "card_selector",
    "title_selector",
    "price_selector",
    "link_selector",
)


def validate_yaml_adapter_config(config: dict) -> list[str]:
    """Return a list of validation error messages (empty list = OK)."""
    errors: list[str] = []
    for key in REQUIRED_KEYS:
        v = config.get(key)
        if not isinstance(v, str) or not v.strip():
            errors.append(f"missing or non-string field: {key}")
    if "search_url_template" in config and "{keyword}" not in config["search_url_template"]:
        errors.append("search_url_template must contain {keyword} placeholder")
    return errors


class YamlHtmlAdapter(HtmlSearchAdapter):
    """Generic HTML adapter whose selectors come from the shops.config_json blob."""

    slug = "yaml-shop"  # overridden by registry
    display_name = "YAML shop"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config=config)
        c = self.config or {}
        # Class-level fields on HtmlSearchAdapter — override per instance
        self.search_url_template = c.get("search_url_template", "")
        self.card_selector = c.get("card_selector", "")
        self.title_selector = c.get("title_selector", "")
        self.price_selector = c.get("price_selector", "")
        self.link_selector = c.get("link_selector", "")
        self.image_selector = c.get("image_selector", "")
        self.specs_selector = c.get("specs_selector", "")
