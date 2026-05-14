from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, desc, select
from sse_starlette.sse import EventSourceResponse

from app.adapters.registry import load_enabled_adapters
from app.adapters.types import ParsedConditions
from app.db.models import ClickLog, SearchHistory
from app.db.session import engine, get_session, init_db
from app.llm.regex_parser import parse as regex_parse
from app.search import run_search

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "web" / "templates"
STATIC_DIR = BASE_DIR / "web" / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


def _parsed_panel_payload(conditions: ParsedConditions, parsed_by: str) -> dict:
    is_empty = not any(
        [
            conditions.category,
            conditions.color,
            conditions.size,
            conditions.material,
            conditions.fit,
            conditions.max_price,
            conditions.free_text,
        ]
    )
    return {"c": conditions, "parsed_empty": is_empty, "parsed_by": parsed_by}


def create_app() -> FastAPI:
    app = FastAPI(title="ShopFinder", version="0.1.0", lifespan=lifespan)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, q: str = "") -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", {"initial_query": q})

    @app.post("/parse", response_class=HTMLResponse)
    def parse_query(request: Request, q: str = Form(default="")) -> HTMLResponse:
        conditions = regex_parse(q)
        return templates.TemplateResponse(
            request,
            "partials/parsed_fields.html",
            _parsed_panel_payload(conditions, "regex"),
        )

    @app.get("/search/stream")
    async def search_stream(request: Request, q: str = ""):
        conditions = regex_parse(q)

        # Persist search_history row up front so click logs can reference it.
        history_row: SearchHistory | None = None
        if q.strip():
            with Session(engine) as session:
                history_row = SearchHistory(
                    raw_query=q,
                    parsed_conditions_json=conditions.model_dump_json(),
                    parsed_by="regex",
                )
                session.add(history_row)
                session.commit()
                session.refresh(history_row)

        # Load adapters in a separate short-lived session (the stream lifetime is
        # longer than a normal request, so we don't want to hold the connection).
        with Session(engine) as session:
            adapters = load_enabled_adapters(session)
        card_template = templates.get_template("partials/result_card.html")

        async def event_stream():
            t0 = time.monotonic()
            total_results = 0
            try:
                # Send a meta event first so the client can record the history_id.
                yield {
                    "event": "meta",
                    "data": json.dumps(
                        {"history_id": history_row.id if history_row else None},
                        ensure_ascii=False,
                    ),
                }

                async for event in run_search(conditions, adapters):
                    if await request.is_disconnected():
                        break
                    if event.kind == "result" and event.result is not None:
                        total_results += 1
                        data = card_template.render(r=event.result)
                        yield {"event": "result", "data": data}
                    elif event.kind in ("shop_started", "shop_completed", "shop_failed"):
                        payload = {"slug": event.shop_slug, "message": event.message or ""}
                        yield {
                            "event": event.kind,
                            "data": json.dumps(payload, ensure_ascii=False),
                        }
                    elif event.kind == "done":
                        yield {"event": "done", "data": ""}
            finally:
                if history_row is not None:
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    with Session(engine) as session:
                        row = session.get(SearchHistory, history_row.id)
                        if row is not None:
                            row.total_results = total_results
                            row.elapsed_ms = elapsed_ms
                            session.add(row)
                            session.commit()

        return EventSourceResponse(event_stream())

    @app.post("/click")
    def log_click(
        history_id: int = Form(...),
        shop_slug: str = Form(...),
        product_url: str = Form(...),
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        history = session.get(SearchHistory, history_id)
        if history is None:
            raise HTTPException(status_code=404, detail="search history not found")
        session.add(
            ClickLog(
                search_history_id=history_id,
                shop_slug=shop_slug,
                result_url=product_url,
            )
        )
        session.commit()
        return JSONResponse({"ok": True})

    @app.get("/history", response_class=HTMLResponse)
    def history_page(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        rows = session.exec(
            select(SearchHistory).order_by(desc(SearchHistory.created_at)).limit(50)
        ).all()
        return templates.TemplateResponse(
            request,
            "history.html",
            {"rows": rows},
        )

    return app


app = create_app()
