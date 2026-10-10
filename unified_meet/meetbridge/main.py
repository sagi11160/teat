from __future__ import annotations

import logging
import re
import secrets
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .api import auth, live, ops, rooms
from .api.deps import Services
from .bridge import BotRunner, PlaywrightBotRunner, Supervisor
from .config import Settings, get_settings
from .db import init_engine
from .hub import EventBus, Hub
from .logging_setup import configure_logging
from .migrate import upgrade_to_head
from .providers import ProviderRegistry
from .security import configure_secret

log = logging.getLogger("meetbridge")
WEB_DIR = Path(__file__).parent / "web"

RunnerFactory = Callable[[Settings, Hub, ProviderRegistry], BotRunner]

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self' blob:; connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


_TOKEN_PATH = re.compile(r"^(/j/|/call/|/api/invite/|/ws/call/)[^/?]+")
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def redact_path(path: str) -> str:
    """Invite tokens grant access to a room, so they never go into logs."""
    return _TOKEN_PATH.sub(r"\1***", path)


def create_app(settings: Settings | None = None, runner_factory: RunnerFactory | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    problems = settings.validate_for_startup()
    if problems:
        raise SystemExit("Configuration error:\n  - " + "\n  - ".join(problems))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        upgrade_to_head(settings.database_url)
        init_engine(settings.database_url)
        configure_secret(settings.secret_key, settings.data_dir)
        bus = EventBus()
        hub = Hub(bus, settings.ice_servers, settings.max_participants_per_room)
        registry = ProviderRegistry(settings)
        runner = (runner_factory or PlaywrightBotRunner)(settings, hub, registry)
        supervisor = Supervisor(settings, runner, bus)
        app.state.services = Services(settings, hub, bus, registry, supervisor)
        await supervisor.start()
        log.info("MeetBridge %s started (env=%s, providers=%s)", __version__, settings.env,
                 ",".join(a.provider.value for a in registry.enabled()))
        try:
            yield
        finally:
            await supervisor.shutdown()
            log.info("MeetBridge stopped")

    app = FastAPI(title="MeetBridge", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID.fullmatch(incoming) else secrets.token_hex(8)
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), display-capture=()"
        if not request.url.path.startswith("/mock-meet"):
            response.headers["Content-Security-Policy"] = _CSP
        if settings.public_base_url.startswith("https://"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if not request.url.path.startswith("/static/"):
            log.info("%s %s %s %.0fms rid=%s", request.method, redact_path(request.url.path), response.status_code,
                     (time.perf_counter() - started) * 1000, request_id)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
        detail = {**detail, "request_id": getattr(request.state, "request_id", None)}
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()
        ]
        return JSONResponse(
            {"error": {"code": "validation_error", "message": "Some fields are invalid", "fields": fields,
                       "request_id": getattr(request.state, "request_id", None)}},
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", None)
        log.exception("unhandled error rid=%s", rid)
        return JSONResponse(
            {"error": {"code": "internal_error", "message": "Something went wrong on our side", "request_id": rid}},
            status_code=500,
        )

    app.include_router(ops.router)
    app.include_router(auth.router)
    app.include_router(rooms.router)
    app.include_router(live.router)
    if settings.enable_mock_provider:
        from . import mockmeet

        app.include_router(mockmeet.router)

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    def page(name: str):
        def handler():
            return FileResponse(WEB_DIR / name, headers={"Cache-Control": "no-store"})

        return handler

    app.get("/", include_in_schema=False)(page("index.html"))
    app.get("/rooms/{room_id}", include_in_schema=False)(page("room.html"))
    app.get("/j/{token}", include_in_schema=False)(page("join.html"))
    app.get("/call/{token}", include_in_schema=False)(page("call.html"))
    return app


def run() -> None:
    import uvicorn

    settings = get_settings()
    # The request middleware logs every request with invite tokens redacted, so
    # uvicorn's own access log (full URLs) stays off.
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None, access_log=False)


if __name__ == "__main__":
    run()
