"""Model-registry read API.

Two endpoints, both read-only projections of what training registered:

* ``GET /api/v1/models`` - registered model configurations, paginated.
* ``GET /api/v1/models/{model_id}`` - one configuration with its training runs.

A run records the chronological training window, the sample size and the
resulting metrics - the audit trail a prediction response points at. Runs
are served oldest first so a walk-forward sequence reads in order.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import SessionDep
from app.api.v1.common import MAX_PAGE_SIZE
from app.modeling.horizons import HORIZONS, horizon_sessions
from app.models.model import Model, ModelRun

router = APIRouter(prefix="/models", tags=["models"])


class ModelSummary(BaseModel):
    """One registered model configuration for one horizon."""

    id: int
    name: str
    algorithm: str
    feature_version: str
    horizon: str = Field(description="Forecast horizon this configuration serves")
    params: dict[str, Any]
    artifact_path: str | None = None
    is_active: bool
    created_at: str
    updated_at: str


class ModelListResponse(BaseModel):
    """A page of registered models, ordered by name."""

    items: list[ModelSummary]
    total: int = Field(description="Total models matching the filter, ignoring pagination")
    limit: int
    offset: int


class ModelRunResponse(BaseModel):
    """One training event for a registered model."""

    id: int
    train_start: date
    train_end: date
    n_train: int
    n_features: int
    metrics: dict[str, Any]
    status: str
    notes: str | None = None
    created_at: str


class ModelDetailResponse(ModelSummary):
    """One configuration with its training runs, oldest first."""

    runs: list[ModelRunResponse]


def _to_summary(model: Model) -> ModelSummary:
    """Serialise a model row without inventing training state."""
    return ModelSummary(
        id=model.id,
        name=model.name,
        algorithm=model.algorithm,
        feature_version=model.feature_version,
        horizon=model.horizon,
        params=model.params,
        artifact_path=model.artifact_path,
        is_active=model.is_active,
        created_at=model.created_at.isoformat(),
        updated_at=model.updated_at.isoformat(),
    )


def _to_run_response(run: ModelRun) -> ModelRunResponse:
    """Serialise one training run."""
    return ModelRunResponse(
        id=run.id,
        train_start=run.train_start,
        train_end=run.train_end,
        n_train=run.n_train,
        n_features=run.n_features,
        metrics=run.metrics,
        status=run.status,
        notes=run.notes,
        created_at=run.created_at.isoformat(),
    )


@router.get("", response_model=ModelListResponse, summary="List registered models")
@router.get(
    "/",
    response_model=ModelListResponse,
    include_in_schema=False,
    summary="List registered models (trailing slash)",
)
def list_models(
    session: SessionDep,
    algorithm: Annotated[str | None, Query(description="Exact algorithm key.")] = None,
    horizon: Annotated[str | None, Query(description="Exact horizon key.")] = None,
    include_inactive: Annotated[bool, Query(description="Also return deactivated models.")] = False,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ModelListResponse:
    """Return a page of the model registry, ordered by name and horizon."""
    filters: list[Any] = []
    if not include_inactive:
        filters.append(Model.is_active.is_(True))
    if algorithm is not None:
        filters.append(Model.algorithm == algorithm)
    if horizon is not None:
        try:
            horizon_sessions(horizon)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "unknown_horizon",
                    "horizon": horizon,
                    "message": f"Served horizons: {sorted(HORIZONS)}.",
                },
            ) from exc
        filters.append(Model.horizon == horizon)
    total = session.scalar(select(func.count()).select_from(Model).where(*filters)) or 0
    rows = session.scalars(
        select(Model)
        .where(*filters)
        .order_by(Model.name, Model.horizon)
        .limit(limit)
        .offset(offset)
    ).all()
    return ModelListResponse(
        items=[_to_summary(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{model_id}", response_model=ModelDetailResponse, summary="Get one model")
def get_model(session: SessionDep, model_id: int) -> ModelDetailResponse:
    """Return one model configuration with its training runs.

    Raises:
        HTTPException: ``404`` when the id is not registered.
    """
    model = session.scalar(select(Model).where(Model.id == model_id))
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "model_not_found",
                "model_id": model_id,
                "message": f"Model id {model_id} is not registered.",
            },
        )
    runs = session.scalars(
        select(ModelRun).where(ModelRun.model_id == model.id).order_by(ModelRun.id)
    ).all()
    return ModelDetailResponse(
        **_to_summary(model).model_dump(), runs=[_to_run_response(run) for run in runs]
    )


__all__ = ["ModelDetailResponse", "ModelListResponse", "ModelRunResponse", "ModelSummary", "router"]
