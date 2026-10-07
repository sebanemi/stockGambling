"""Version 1 of the StockGambling HTTP API.

Health and, since Phase 2, the CEDEAR metadata routes are wired up. The
remaining domain routes (``/models``, ``/backtests``, ``/experiments``) are
added as the corresponding pipeline stages are implemented, without changing
this module's prefix or the shared response envelope.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import __version__
from app.api.deps import SettingsDep
from app.api.v1 import cedears, health

api_router = APIRouter()

# Unversioned liveness lives at /health/* so container probes stay stable even
# if the versioned API prefix ever changes.
health_router = health.router


class ApiInfo(BaseModel):
    """Service discovery payload."""

    service: str
    version: str
    environment: str
    api_prefix: str
    docs_url: str | None = None
    phase: str = Field(description="Current development phase of the platform")


@api_router.get("", response_model=ApiInfo, summary="API discovery document")
@api_router.get("/", response_model=ApiInfo, include_in_schema=False)
def api_info(settings: SettingsDep) -> ApiInfo:
    """Return basic service metadata and the documentation URL."""
    return ApiInfo(
        service=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        api_prefix=settings.api_prefix,
        docs_url="/docs" if settings.docs_enabled else None,
        phase="6-baseline-models",
    )


api_router.include_router(health_router, prefix="/health")
api_router.include_router(cedears.router)
