"""KST timestamp display in history page."""
from datetime import datetime, timezone

from app.main import _to_kst


def test_to_kst_handles_naive_utc() -> None:
    # 2026-05-14 12:00 UTC == 2026-05-14 21:00 KST
    naive = datetime(2026, 5, 14, 12, 0, 0)
    assert _to_kst(naive) == "2026-05-14 21:00"


def test_to_kst_handles_aware_utc() -> None:
    aware = datetime(2026, 1, 1, 0, 30, 0, tzinfo=timezone.utc)
    # 2026-01-01 00:30 UTC == 2026-01-01 09:30 KST
    assert _to_kst(aware) == "2026-01-01 09:30"


def test_to_kst_returns_empty_for_none() -> None:
    assert _to_kst(None) == ""
