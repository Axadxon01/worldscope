"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker

from worldscope import __version__
from worldscope.api.middleware import request_context
from worldscope.api.routers import health, system
from worldscope.core.config import Settings, get_settings
from worldscope.core.logging import configure_logging, get_logger
from worldscope.core.redis import create_redis
from worldscope.db.session import create_api_engine

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    is_production = settings.environment == "production"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Pools are created here so they belong to the server's event loop.
        engine = create_api_engine(settings.database_url)
        app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        app.state.redis = create_redis(settings.redis_url)
        log.info("api_starting", environment=settings.environment, version=__version__)
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await engine.dispose()
            log.info("api_stopped")

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        # Interactive docs only outside production: no need to publish the API map.
        docs_url=None if is_production else "/docs",
        redoc_url=None,
        openapi_url=None if is_production else "/openapi.json",
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-Request-ID", "X-CSRF-Token"],
        )
    app.middleware("http")(request_context)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log the cause server-side; never return internals to the client.
        log.exception("unhandled_error", path=request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    app.include_router(health.router)
    app.include_router(system.router)
    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn --factory worldscope.api.main:app_factory`."""
    return create_app()
