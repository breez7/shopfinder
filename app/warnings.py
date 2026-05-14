"""Adapter warning helpers (issue #21).

Adapters use `record_warning` to surface degraded states (HTTP errors, parse
exceptions, bot-detection signatures, suspicious empty results). The home page
banner and `/admin/warnings` UI both read from `adapter_warnings`.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, desc, select

from app.db.models import AdapterWarning
from app.db.session import engine

KIND_HTTP_ERROR = "http_error"
KIND_PARSE_EXCEPTION = "parse_exception"
KIND_BOT_DETECTION = "bot_detection_suspected"
KIND_ZERO_RESULTS = "zero_results_suspicious"

# Soft cap so the table can't balloon
MAX_ROWS = 1000


def record_warning(
    shop_slug: str,
    kind: str,
    message: str,
    snippet: str = "",
) -> None:
    """Insert a warning row in its own short-lived session.

    Best-effort: errors are swallowed so warning-logging can never break a
    search request. Idempotent for repeated identical warnings within a
    minute (avoids storms when a shop is fully down).
    """
    try:
        with Session(engine) as session:
            recent = session.exec(
                select(AdapterWarning)
                .where(
                    AdapterWarning.shop_slug == shop_slug,
                    AdapterWarning.kind == kind,
                    AdapterWarning.message == message,
                    AdapterWarning.raised_at >= datetime.utcnow() - timedelta(minutes=1),
                )
                .limit(1)
            ).first()
            if recent is not None:
                return

            session.add(
                AdapterWarning(
                    shop_slug=shop_slug,
                    kind=kind,
                    message=message[:500],
                    snippet=(snippet or "")[:500],
                )
            )
            session.commit()

            # Trim the oldest rows if we're over the soft cap
            total = session.exec(select(AdapterWarning)).all()
            if len(total) > MAX_ROWS:
                excess = sorted(total, key=lambda r: r.raised_at)[: len(total) - MAX_ROWS]
                for row in excess:
                    session.delete(row)
                session.commit()
    except Exception:  # noqa: BLE001
        # Best-effort: never propagate from telemetry-style code
        pass


def recent_unresolved(session: Session, days: int = 7) -> list[AdapterWarning]:
    cutoff = datetime.utcnow() - timedelta(days=days)
    return list(
        session.exec(
            select(AdapterWarning)
            .where(
                AdapterWarning.dismissed == False,  # noqa: E712
                AdapterWarning.raised_at >= cutoff,
            )
            .order_by(desc(AdapterWarning.raised_at))
            .limit(100)
        ).all()
    )


def dismiss(session: Session, warning_id: int) -> bool:
    row = session.get(AdapterWarning, warning_id)
    if row is None:
        return False
    row.dismissed = True
    session.add(row)
    session.commit()
    return True


def clear_all(session: Session) -> int:
    rows = session.exec(select(AdapterWarning)).all()
    count = len(rows)
    for r in rows:
        session.delete(r)
    session.commit()
    return count
