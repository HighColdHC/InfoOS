"""FastAPI app factory for the InfoOS web UI.

``create_app`` is the only entry point: it registers the per-domain
routers via ``backend.api.main``, serves the built React assets when
available, and returns the assembled ``FastAPI`` instance.

Route implementations live in ``backend/api/routes/*.py``,
and read models live in ``backend/read_models/``. This file stays small
on purpose: any new endpoint should be added to a route module, not here.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from os import PathLike, stat_result
from threading import Lock

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from backend.api.main import api_router
from backend.api.mcp.auth import McpBearerMiddleware
from backend.api.mcp.server import create_mcp_server
from backend.api.middleware.plugin_request_audit import PluginRequestAuditMiddleware
from backend.api.security import SameOriginManagementMiddleware
from backend.app.gzip import SelectiveGZipMiddleware
from backend.infrastructure.storage.paths import data_dir
from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
from backend.infrastructure.observability.logging import configure_logging, get_logger


class CachedFrontendAssets(StaticFiles):
    def file_response(
        self, full_path: PathLike, stat_result: stat_result, scope: Scope, status_code: int = 200
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def create_app(base_dir: Path | str = ".") -> FastAPI:
    app_base_dir = Path(base_dir)
    # Synchronous initialization is intentional: app construction is the
    # cold-start boundary, and schema/migration failures must fail fast.
    initialize_runtime_storage(app_base_dir)
    app_data_dir = data_dir()
    log_path = configure_logging(app_base_dir)
    logger = get_logger("backend.app")
    mcp = create_mcp_server(app_base_dir)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="InfoOS", lifespan=lifespan)
    app.state.base_dir = app_base_dir
    app.state.data_dir = app_data_dir
    app.state.log_path = log_path
    app.add_middleware(McpBearerMiddleware)
    app.add_middleware(PluginRequestAuditMiddleware)
    app.add_middleware(SameOriginManagementMiddleware)
    app.add_middleware(SelectiveGZipMiddleware)
    app.include_router(api_router)
    frontend_dist_dir = app_base_dir / "frontend" / "dist"
    frontend_assets_dir = frontend_dist_dir / "assets"
    if frontend_assets_dir.exists():
        app.mount(
            "/assets",
            CachedFrontendAssets(directory=str(frontend_assets_dir)),
            name="frontend-assets",
        )
    app.mount("/", mcp_app, name="mcp")
    logger.info(
        "app_started base_dir=%s data_dir=%s log_path=%s",
        app_base_dir,
        app_data_dir,
        log_path,
    )
    return app


_app: FastAPI | None = None
_app_lock = Lock()


def __getattr__(name: str) -> object:
    """Lazily construct the ASGI app for ``backend.app.main:app``."""
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    global _app
    with _app_lock:
        if _app is None:
            _app = create_app()
        return _app
