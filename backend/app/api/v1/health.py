"""Health and readiness endpoints.

Two distinct concepts, deliberately separated:

* **Liveness** - "is the process running?". Never touches dependencies, so a
  slow database cannot cause the orchestrator to kill a healthy API.
* **Readiness** - "can the process serve traffic?". Verifies PostgreSQL and
  Redis. Returns ``503`` when degraded, which is what Compose/Kubernetes need.
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from app import __version__
from app.api.deps import SettingsDep
from app.db.session import ping_database
from app.infra.cache import ping_redis

router = APIRouter(tags=["health"])

Status = Literal["ok", "degraded", "unavailable"]


class LivenessResponse(BaseModel):
    """Payload of the liveness probe."""

    status: Status = "ok"
    service: str
    version: str
    environment: str
    timestamp: float = Field(description="Unix timestamp of the probe")


class ComponentHealth(BaseModel):
    """Health of a single infrastructure dependency."""

    status: Status
    latency_ms: float
    detail: str | None = None


class ReadinessResponse(BaseModel):
    """Payload of the readiness probe."""

    status: Status
    service: str
    version: str
    environment: str
    checks: dict[str, ComponentHealth]


@router.get("/live", response_model=LivenessResponse, summary="Liveness probe")
def liveness(settings: SettingsDep) -> LivenessResponse:
    """Return immediately; does not touch PostgreSQL or Redis."""
    return LivenessResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        timestamp=time.time(),
    )


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
def readiness(response: Response, settings: SettingsDep) -> ReadinessResponse:
    """Verify that every required dependency is reachable.

    Returns ``503`` when any dependency is down so that Compose, an ingress
    controller or a load balancer stops routing traffic to this instance.
    """
    checks: dict[str, ComponentHealth] = {}

    for name, probe in (("postgres", ping_database), ("redis", ping_redis)):
        started = time.perf_counter()
        healthy = probe()
        checks[name] = ComponentHealth(
            status="ok" if healthy else "unavailable",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    overall: Status = "ok" if all(c.status == "ok" for c in checks.values()) else "degraded"
    if overall != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status=overall,
        service=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        checks=checks,
    )
