from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db.session import init_db
from app.llm.regex_parser import parse as regex_parse

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "web" / "templates"
STATIC_DIR = BASE_DIR / "web" / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


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
        return templates.TemplateResponse(
            request,
            "partials/parsed_fields.html",
            {
                "c": conditions,
                "parsed_empty": is_empty,
                "parsed_by": "regex",
            },
        )

    return app


app = create_app()
