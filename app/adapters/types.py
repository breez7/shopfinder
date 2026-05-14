from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ParsedConditions(BaseModel):
    """Structured search conditions extracted from a natural-language query."""

    category: Optional[str] = None
    color: Optional[str] = None
    size: Optional[str] = None
    material: Optional[str] = None
    material_pct: Optional[int] = None
    fit: Optional[str] = None
    max_price: Optional[int] = None
    free_text: str = ""
    # Set by the LLM query optimizer (#17). When present, adapters use this
    # verbatim instead of the naive concat.
    keyword_override: Optional[str] = None

    def keyword(self) -> str:
        """Keyword for the adapter's search URL. Honors LLM optimizer override."""
        if self.keyword_override:
            return self.keyword_override
        parts: list[str] = []
        for value in (self.color, self.size, self.category, self.fit, self.material):
            if value:
                parts.append(value)
        if self.free_text:
            parts.append(self.free_text)
        return " ".join(parts).strip()


class SearchResult(BaseModel):
    """One item returned by an adapter. error=True marks a failure surfaced to the UI."""

    shop_slug: str
    title: str = ""
    price: Optional[int] = None
    currency: str = "KRW"
    image_url: Optional[str] = None
    product_url: str = ""
    raw_specs: str = ""
    match_score: Optional[float] = None
    matched_reason: Optional[str] = None
    error: bool = False
    error_message: Optional[str] = None

    @classmethod
    def make_error(cls, shop_slug: str, message: str) -> "SearchResult":
        return cls(shop_slug=shop_slug, error=True, error_message=message)


class AdapterEvent(BaseModel):
    """High-level event emitted by the search orchestrator."""

    kind: str = Field(description="shop_started | shop_completed | shop_failed | result | done")
    shop_slug: Optional[str] = None
    result: Optional[SearchResult] = None
    message: Optional[str] = None
