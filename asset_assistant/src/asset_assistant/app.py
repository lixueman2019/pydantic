"""Single-origin, loopback-only composition root and eight public routes."""

from __future__ import annotations

import asyncio
import contextlib
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Path as ApiPath, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException

from .agent import AzureModelGateway, public_failure
from .config import Settings
from .contracts import (
    AppError, ChatRequest, ErrorCode, ErrorResponse, FailedPayload,
    HealthResponse, ModelGateway, RunError, RunStarted, SessionResponse, SSEEvent,
    document_paths,
)
from .sessions import SessionStore, opaque_id


STATIC_ALLOWLIST = frozenset({
    "app.js", "styles.css", "api/index.js", "api/liveClient.js", "api/errors.js",
    "api/sse.js", "api/types.js",
    "vendor/pico-2.1.1.min.css",
    *{f"vendor/icons/{name}.svg" for name in (
        "activity", "chart-no-axes-combined", "file-search", "messages-square",
        "moon", "plus", "send", "shield-check", "square", "sun", "wind")},
})
IdPath = Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9_-]{32,64}$")]


def error_response(error: AppError) -> JSONResponse:
    return JSONResponse(ErrorResponse(error=error.error).model_dump(mode="json"), status_code=error.status)


def create_app(settings: Settings | None = None, gateway: ModelGateway | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = SessionStore(settings)
    knowledge = tools = None
    # Modules may be delivered by parallel agents. No fake fallback.
    try:
        from .knowledge import KnowledgeService
        from .tools.service import ToolService

        knowledge = KnowledgeService(settings.root)
        tools = ToolService(settings.root)
    except Exception:
        pass  # Health stays available; inference explicitly fails DATA_NOT_READY.
    model = gateway or AzureModelGateway(settings, store, knowledge, tools)

    async def janitor() -> None:
        while True:
            await asyncio.sleep(min(30, settings.session_ttl_seconds))
            store.reap()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(janitor())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await store.close()
            await model.close()

    app = FastAPI(title="Asset Performance Assistant", docs_url=None,
                  redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.sessions = store
    app.state.gateway = model

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        hosts = request.headers.getlist("host")
        host = hosts[0] if len(hosts) == 1 else ""
        if not re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?", host):
            return error_response(AppError(ErrorCode.FORBIDDEN, "仅允许本地 Host。", 403))
        if request.method in {"POST", "DELETE"}:
            origins = request.headers.getlist("origin")
            origin = origins[0] if len(origins) == 1 else None
            fetch_site = request.headers.get("sec-fetch-site")
            expected = f"{request.url.scheme}://{host}"
            if (len(origins) > 1 or (origin is not None and origin != expected)
                    or fetch_site == "cross-site"
                    or (origin is None and (fetch_site is not None or "mozilla" in request.headers.get("user-agent", "").lower()))):
                return error_response(AppError(ErrorCode.FORBIDDEN, "拒绝跨源或无法验证来源的浏览器写请求。", 403))
            if request.method == "POST":
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 40000:
                        return error_response(AppError(ErrorCode.BAD_REQUEST, "请求体超出大小限制。", 413))
                request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError):
        return error_response(AppError(ErrorCode.VALIDATION_ERROR, "请求格式或字段不合法。", 422))

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return error_response(AppError(ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.BAD_REQUEST,
                                       "请求的资源或方法不可用。", exc.status_code))

    @app.exception_handler(Exception)
    async def unknown_error(_: Request, __: Exception):
        return error_response(AppError(ErrorCode.INTERNAL_ERROR, "本地请求失败。"))

    def safe_file(base: Path, relative: str) -> Path:
        candidate = (base / relative).resolve()
        if not candidate.is_relative_to(base.resolve()) or not candidate.is_file():
            raise AppError(ErrorCode.NOT_FOUND, "资源不存在。", 404)
        return candidate

    @app.get("/")
    async def index():
        return FileResponse(safe_file(settings.static_dir, "index.html"), media_type="text/html")

    @app.get("/static/{asset_id:path}")
    async def static(asset_id: str):
        if asset_id not in STATIC_ALLOWLIST:
            raise AppError(ErrorCode.NOT_FOUND, "静态资源不在允许列表中。", 404)
        return FileResponse(safe_file(settings.static_dir, asset_id))

    @app.get("/api/health", response_model=HealthResponse)
    async def health():
        return HealthResponse(
            model_configured=settings.model_configured, model_verified=model.verified,
            model_verified_at=model.verified_at,
            model_status=("verified" if model.verified else "unverified") if settings.model_configured else "not_configured",
            data_sources_ready=bool(knowledge and knowledge.ready and tools),
            configuration_issues=settings.configuration_issues,
            reference_date=tools.reference_date if tools else None,
        )

    @app.post("/api/sessions", response_model=SessionResponse, status_code=201)
    async def create_session():
        return SessionResponse(session_id=store.create().session_id,
                       reference_date=tools.reference_date if tools else None)

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: IdPath):
        store.delete(session_id)
        return Response(status_code=204)

    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request):
        # Fail before returning SSE/200; prepare cannot contact the model.
        try:
            model.prepare()
        except Exception as exc:
            raise public_failure(exc) from exc
        session = store.acquire(body.session_id)
        run_id = opaque_id()

        async def generate():
            queue: asyncio.Queue = asyncio.Queue(maxsize=32)

            async def produce():
                started = asyncio.get_running_loop().time()
                deadline = asyncio.timeout(settings.run_timeout_seconds)
                tool_events = 0
                citation_events = 0
                try:
                    async with deadline:
                        async for event in model.stream(body.message, session):
                            tool_events += event.type == "tool_started"
                            citation_events += event.type == "citation"
                            await queue.put(event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failure = public_failure(exc)
                    if deadline.expired():
                        elapsed = round(asyncio.get_running_loop().time() - started, 1)
                        failure = AppError(
                            ErrorCode.RUN_TIMEOUT,
                            f"本轮已等待约 {elapsed:g} 秒，达到 {settings.run_timeout_seconds} 秒整轮预算；"
                            "回答未完成，已收集引用不等于最终答案。可缩小问题范围后手动重试，或调整 "
                            "ASSET_ASSISTANT_RUN_TIMEOUT_SECONDS 并重启；不会自动重放请求。", 503)
                        # Explicit numeric metadata only; never exception/request contents.
                        failure.error.details = {
                            "timeout_scope": "run", "elapsed_seconds": elapsed,
                            "run_timeout_seconds": settings.run_timeout_seconds,
                            "tool_calls_started": tool_events, "citations_received": citation_events,
                        }
                    await queue.put(RunError(payload=FailedPayload(error=failure.error)))
                finally:
                    # Do not block cancellation on a full queue.
                    if not asyncio.current_task().cancelling():
                        await queue.put(None)

            producer = asyncio.create_task(produce())
            session.task = producer
            seq = 1
            try:
                yield SSEEvent.wrap(run_id, seq, RunStarted()).encode()
                while True:
                    if await request.is_disconnected() or session.deleted:
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=0.25)
                    except TimeoutError:
                        if producer.done() and queue.empty():
                            break
                        continue
                    if event is None:
                        break
                    seq += 1
                    yield SSEEvent.wrap(run_id, seq, event).encode()
                    if event.type in {"run_completed", "run_error"}:
                        break
            finally:
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer
                store.release(session)

        return StreamingResponse(generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/documents/{source_id}")
    async def document(source_id: str):
        allowed = document_paths(settings.root)
        if source_id not in allowed:
            raise AppError(ErrorCode.NOT_FOUND, "未知文档标识。", 404)
        path = safe_file(settings.root, allowed[source_id].name)
        return FileResponse(path, media_type="application/pdf")

    @app.get("/api/sessions/{session_id}/charts/{chart_id}")
    async def chart(session_id: IdPath, chart_id: IdPath):
        png = store.get(session_id).charts.get(chart_id)
        if png is None:
            raise AppError(ErrorCode.NOT_FOUND, "此会话没有该图表。", 404)
        return Response(png, media_type="image/png")

    return app