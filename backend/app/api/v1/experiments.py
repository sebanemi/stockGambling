"""Experiment read API.

``GET /api/v1/experiments`` serves the training-run audit trail as
experiment records: each entry carries the model identity, the
chronological training window, the sample size and the resulting metrics.
Every value comes from the ``sg_model_runs`` rows training wrote - the
endpoint pages them, newest first, and fabricates nothing.

A dedicated walk-forward experiment table (fold definitions, feature
version, data period per run) remains future work; until then the run
rows are the experiment record.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import SessionDep
from app.api.v1.common import MAX_PAGE_SIZE
from app.models.model import Model, ModelRun

router = APIRouter(prefix="/experiments", tags=["experiments"])


class ExperimentResponse(BaseModel):
    """One training run as an experiment record."""

    id: int
    model_id: int
    model_name: str
    algorithm: str
    feature_version: str
    horizon: str = Field(description="Forecast horizon the run's model serves")
    train_start: date
    train_end: date
    n_train: int
    n_features: int
    metrics: dict[str, Any]
    status: str
    notes: str | None = None
    created_at: str


class ExperimentListResponse(BaseModel):
    """A page of experiment records, newest first."""

    items: list[ExperimentResponse]
    total: int = Field(description="Total runs matching the filter, ignoring pagination")
    limit: int
    offset: int


@router.get("", response_model=ExperimentListResponse, summary="List experiments")
@router.get(
    "/",
    response_model=ExperimentListResponse,
    include_in_schema=False,
    summary="List experiments (trailing slash)",
)
def list_experiments(
    session: SessionDep,
    model_name: Annotated[str | None, Query(description="Exact registered model name.")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ExperimentListResponse:
    """Return experiment records (training runs), newest first."""
    stmt = (
        select(ModelRun, Model)
        .join(Model, ModelRun.model_id == Model.id)
        .order_by(ModelRun.id.desc())
    )
    if model_name is not None:
        stmt = stmt.where(Model.name == model_name.strip())
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pairs = session.execute(stmt.limit(limit).offset(offset)).all()
    return ExperimentListResponse(
        items=[
            ExperimentResponse(
                id=run.id,
                model_id=run.model_id,
                model_name=model.name,
                algorithm=model.algorithm,
                feature_version=model.feature_version,
                horizon=model.horizon,
                train_start=run.train_start,
                train_end=run.train_end,
                n_train=run.n_train,
                n_features=run.n_features,
                metrics=run.metrics,
                status=run.status,
                notes=run.notes,
                created_at=run.created_at.isoformat(),
            )
            for run, model in pairs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


__all__ = ["ExperimentListResponse", "ExperimentResponse", "router"]
