from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from app.adapters.registry import load_enabled_adapters
from app.adapters.types import ParsedConditions
from app.db.session import get_session, init_db
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
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html")

    @app.post("/parse", response_class=HTMLResponse)
    def parse_query(request: Request, q: str = Form(default="")) -> HTMLResponse:
        conditions = regex_parse(q)
        return templates.TemplateResponse(
            request,
            "partials/parsed_fields.html",
            _parsed_panel_payload(conditions, "regex"),
        )

    @app.get("/search/stream")
    async def search_stream(request: Request, q: str = "", session=Depends(get_session)):
        conditions = regex_parse(q)
        adapters = load_enabled_adapters(session)
        card_template = templates.get_template("partials/result_card.html")

        async def event_stream():
            async for event in run_search(conditions, adapters):
                if await request.is_disconnected():
                    break
                if event.kind == "result" and event.result is not None:
                    data = card_template.render(r=event.result)
                    yield {"event": "result", "data": data}
                elif event.kind in ("shop_started", "shop_completed", "shop_failed"):
                    payload = {"slug": event.shop_slug, "message": event.message or ""}
                    yield {"event": event.kind, "data": json.dumps(payload, ensure_ascii=False)}
                elif event.kind == "done":
                    yield {"event": "done", "data": ""}

        return EventSourceResponse(event_stream())

    return app


app = create_app()
