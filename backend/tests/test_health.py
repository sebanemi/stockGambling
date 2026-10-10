"""Health, liveness and readiness endpoint tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings

pytestmark = pytest.mark.unit


def test_root_health_alias(client: TestClient) -> None:
    """The unversioned alias exists so any probe URL works."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "stockgambling-api"


def test_liveness_never_fails(client: TestClient) -> None:
    """Liveness must not depend on PostgreSQL or Redis."""
    response = client.get("/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "checks" not in body


def test_liveness_matches_versioned_route(client: TestClient) -> None:
    """``/health/live`` and ``/api/v1/health/live`` must agree."""
    unversioned = client.get("/health/live")
    versioned = client.get("/api/v1/health/live")
    assert unversioned.status_code == versioned.status_code == 200
    assert unversioned.json()["version"] == versioned.json()["version"]


def test_readiness_reports_every_component(client: TestClient) -> None:
    """Readiness always reports both dependencies, healthy or not."""
    response = client.get("/health/ready")
    body = response.json()
    assert set(body["checks"]) == {"postgres", "redis"}
    assert response.status_code in (200, 503)
    assert body["status"] == ("ok" if response.status_code == 200 else "degraded")
    for check in body["checks"].values():
        assert check["latency_ms"] >= 0


@pytest.mark.integration
def test_readiness_is_green_when_dependencies_are_up(client: TestClient) -> None:
    """With PostgreSQL and Redis running the service must report ready."""
    response = client.get("/health/ready")
    assert response.status_code == 200, response.text
    assert all(check["status"] == "ok" for check in response.json()["checks"].values())


def test_api_discovery_document(client: TestClient, settings: Settings) -> None:
    """The discovery document advertises the API prefix, docs URL and phase."""
    response = client.get(settings.api_prefix)
    assert response.status_code == 200
    body = response.json()
    assert body["api_prefix"] == "/api/v1"
    assert body["docs_url"] == "/docs"
    assert body["phase"] == "10-dashboard"


def test_openapi_schema_is_generated(client: TestClient) -> None:
    """OpenAPI generation must not fail (catches bad response models)."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "/health/ready" in schema["paths"]
    assert "/api/v1/health/live" in schema["paths"]
    assert "/api/v1/cedears" in schema["paths"]


def test_request_id_is_propagated(client: TestClient) -> None:
    """An inbound request id is echoed back for log correlation."""
    response = client.get("/health/live", headers={"x-request-id": "test-request-id"})
    assert response.headers["x-request-id"] == "test-request-id"
