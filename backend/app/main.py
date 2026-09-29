"""FastAPI application factory.

Exposes ``app.main:app`` for Uvicorn. The factory pattern keeps the app
importable from tests without any side effects.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from starlette.middleware.base import RequestResponseEndpoint

from app import __version__
from app.api.v1 import health
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.time import utc_now
from app.db.session import dispose_engine
from app.infra.cache import close_redis

logger = get_logger(__name__)

DESCRIPTION = """
**StockGambling** - research platform for predicting the price direction of
Argentine **CEDEARs** traded on BYMA.

The prediction target is the CEDEAR quoted in **ARS**, *not* the underlying
foreign security. A CEDEAR is a distinct instrument whose price is driven by
the underlying asset, the USD/ARS exchange rate, the conversion ratio and
local market microstructure.

All timestamps are timezone-aware and stored in UTC.
"""


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Manage process start-up and shutdown."""
    settings = get_settings()
    configure_logging(settings)
    logger.info(
        "api.startup",
        version=__version__,
        environment=settings.app_env,
        docs_enabled=settings.docs_enabled,
    )
    try:
        yield
    finally:
        dispose_engine()
        close_redis()
        logger.info("api.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings)

    application = FastAPI(
        title="StockGambling API",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    if settings.cors_origin_list:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )

    @application.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Propagate a request id so logs can be correlated end to end."""
        request_id = request.headers.get("x-request-id") or utc_now().isoformat()
        response: Response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    @application.get("/health", include_in_schema=False)
    def root_health() -> dict[str, str]:
        """Alias so a single probe URL works for any environment."""
        return {"status": "ok", "service": settings.app_name, "version": __version__}

    application.include_router(health.router, prefix="/health")
    application.include_router(api_router, prefix=settings.api_prefix)
    return application


app = create_app()
