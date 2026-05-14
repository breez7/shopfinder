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
from app.adapters.types import ParsedConditions, SearchResult
from app.db.models import ClickLog, SearchHistory
from app.db.session import engine, get_session, init_db
from app.llm.client import connection_test as llm_connection_test
from app.llm.llm_parser import parse_with_llm
from app.llm.match_scorer import score_batch
from app.llm.query_optimizer import optimize as optimize_keyword
from app.llm.regex_parser import parse as regex_parse
from app.search import run_search
from app.settings_store import (
    KEY_LLM_API_KEY,
    KEY_LLM_BASE_URL,
    KEY_LLM_CALL_CAP,
    KEY_LLM_MODEL,
    KEY_NAVER_CLIENT_ID,
    KEY_NAVER_CLIENT_SECRET,
    get as settings_get,
    mask as settings_mask,
    set_ as settings_set,
)
from app.warnings import (
    clear_all as warnings_clear_all,
    dismiss as warnings_dismiss,
    recent_unresolved,
)

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
    def index(
        request: Request,
        q: str = "",
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        warnings = recent_unresolved(session)
        return templates.TemplateResponse(
            request,
            "index.html",
            {"initial_query": q, "warnings": warnings},
        )

    @app.post("/parse", response_class=HTMLResponse)
    async def parse_query(request: Request, q: str = Form(default="")) -> HTMLResponse:
        conditions, parsed_by = await parse_with_llm(q)
        return templates.TemplateResponse(
            request,
            "partials/parsed_fields.html",
            _parsed_panel_payload(conditions, parsed_by),
        )

    @app.get("/search/stream")
    async def search_stream(request: Request, q: str = ""):
        # Try LLM parser first; it falls back to regex on any failure.
        conditions, parsed_by = await parse_with_llm(q)

        # Persist search_history row up front so click logs can reference it.
        history_row: SearchHistory | None = None
        if q.strip():
            with Session(engine) as session:
                history_row = SearchHistory(
                    raw_query=q,
                    parsed_conditions_json=conditions.model_dump_json(),
                    parsed_by=parsed_by,
                )
                session.add(history_row)
                session.commit()
                session.refresh(history_row)

        with Session(engine) as session:
            adapters = load_enabled_adapters(session)
        card_template = templates.get_template("partials/result_card.html")

        # LLM query optimizer (#17) — best-effort, no-op when LLM unconfigured.
        per_shop_keyword: dict[str, str] = {}
        for a in adapters:
            try:
                per_shop_keyword[a.slug] = await optimize_keyword(conditions, a.slug)
            except Exception:  # noqa: BLE001
                per_shop_keyword[a.slug] = conditions.keyword()

        async def event_stream():
            t0 = time.monotonic()
            collected: list[SearchResult] = []
            try:
                yield {
                    "event": "meta",
                    "data": json.dumps(
                        {
                            "history_id": history_row.id if history_row else None,
                            "parsed_by": parsed_by,
                        },
                        ensure_ascii=False,
                    ),
                }

                async for event in run_search(
                    conditions, adapters, per_shop_keyword=per_shop_keyword
                ):
                    if await request.is_disconnected():
                        break
                    if event.kind == "result" and event.result is not None:
                        collected.append(event.result)
                        data = card_template.render(r=event.result)
                        yield {"event": "result", "data": data}
                    elif event.kind in ("shop_started", "shop_completed", "shop_failed"):
                        payload = {"slug": event.shop_slug, "message": event.message or ""}
                        yield {
                            "event": event.kind,
                            "data": json.dumps(payload, ensure_ascii=False),
                        }
                    elif event.kind == "done":
                        # Score collected results in batches (#18/#19), then emit
                        # score_update events that the client uses to enrich cards.
                        if collected:
                            try:
                                scored = await score_batch(conditions, collected)
                            except Exception:  # noqa: BLE001
                                scored = collected
                            for item in scored:
                                if item.match_score is None and item.matched_reason is None:
                                    continue
                                yield {
                                    "event": "score_update",
                                    "data": json.dumps(
                                        {
                                            "product_url": item.product_url,
                                            "score": item.match_score,
                                            "reason": item.matched_reason,
                                        },
                                        ensure_ascii=False,
                                    ),
                                }
                        yield {"event": "done", "data": ""}
            finally:
                if history_row is not None:
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    with Session(engine) as session:
                        row = session.get(SearchHistory, history_row.id)
                        if row is not None:
                            row.total_results = len(collected)
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

    @app.get("/admin/warnings", response_class=HTMLResponse)
    def admin_warnings(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        rows = recent_unresolved(session, days=30)
        return templates.TemplateResponse(request, "admin/warnings.html", {"rows": rows})

    @app.post("/admin/warnings/{warning_id}/dismiss")
    def admin_dismiss(warning_id: int, session: Session = Depends(get_session)):
        if not warnings_dismiss(session, warning_id):
            raise HTTPException(status_code=404, detail="warning not found")
        return JSONResponse({"ok": True})

    @app.post("/admin/warnings/clear")
    def admin_clear(session: Session = Depends(get_session)):
        return JSONResponse({"ok": True, "deleted": warnings_clear_all(session)})

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
        api_key = settings_get(session, KEY_LLM_API_KEY)
        naver_secret = settings_get(session, KEY_NAVER_CLIENT_SECRET)
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "llm_base_url": settings_get(session, KEY_LLM_BASE_URL),
                "llm_api_key_masked": settings_mask(api_key),
                "llm_model": settings_get(session, KEY_LLM_MODEL),
                "llm_call_cap": settings_get(session, KEY_LLM_CALL_CAP, "0"),
                "naver_client_id": settings_get(session, KEY_NAVER_CLIENT_ID),
                "naver_secret_masked": settings_mask(naver_secret),
            },
        )

    @app.post("/settings", response_class=HTMLResponse)
    def settings_save(
        llm_base_url: str = Form(default=""),
        llm_api_key: str = Form(default=""),
        llm_model: str = Form(default=""),
        llm_call_cap: str = Form(default="0"),
        naver_client_id: str = Form(default=""),
        naver_client_secret: str = Form(default=""),
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        settings_set(session, KEY_LLM_BASE_URL, llm_base_url.strip())
        settings_set(session, KEY_LLM_MODEL, llm_model.strip())
        settings_set(session, KEY_LLM_CALL_CAP, llm_call_cap.strip() or "0")
        settings_set(session, KEY_NAVER_CLIENT_ID, naver_client_id.strip())
        # API key + Naver secret: empty input means "leave existing value alone"
        if llm_api_key.strip():
            settings_set(session, KEY_LLM_API_KEY, llm_api_key.strip())
        if naver_client_secret.strip():
            settings_set(session, KEY_NAVER_CLIENT_SECRET, naver_client_secret.strip())
        return HTMLResponse("<span style='color:#2ea44f'>저장됨</span>")

    @app.post("/settings/llm-test", response_class=HTMLResponse)
    async def settings_llm_test() -> HTMLResponse:
        ok, msg = await llm_connection_test()
        color = "#2ea44f" if ok else "#cf222e"
        return HTMLResponse(f"<span style='color:{color}'>{msg}</span>")

    return app


app = create_app()
