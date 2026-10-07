"""Model-registry persistence: register models, record training runs.

The database rows are the audit trail (who trained what, on which window,
with which metrics). The fitted bytes live in artifact files written by the
model's own :meth:`save`; ``artifact_path`` points at them.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.model import Model, ModelRun


def register_model(
    session: Session,
    *,
    name: str,
    algorithm: str,
    feature_version: str,
    params: dict[str, Any],
    artifact_path: str | None = None,
) -> Model:
    """Insert (or return the existing) model row for ``name``."""
    existing = session.scalar(select(Model).where(Model.name == name))
    if existing is not None:
        return existing
    model = Model(
        name=name,
        algorithm=algorithm,
        feature_version=feature_version,
        params=params,
        artifact_path=artifact_path,
    )
    session.add(model)
    session.commit()
    return model


def record_run(
    session: Session,
    *,
    model_id: int,
    train_start: date,
    train_end: date,
    n_train: int,
    n_features: int,
    metrics: dict[str, Any],
    status: str = "succeeded",
    notes: str | None = None,
) -> ModelRun:
    """Append a training-run record for ``model_id``."""
    run = ModelRun(
        model_id=model_id,
        train_start=train_start,
        train_end=train_end,
        n_train=n_train,
        n_features=n_features,
        metrics=metrics,
        status=status,
        notes=notes,
    )
    session.add(run)
    session.commit()
    return run


def get_model(session: Session, name: str) -> Model | None:
    """Fetch a registered model by name."""
    return session.scalar(select(Model).where(Model.name == name))


def list_models(session: Session, *, algorithm: str | None = None) -> list[Model]:
    """List registered models, optionally filtered by algorithm."""
    stmt = select(Model).order_by(Model.name)
    if algorithm is not None:
        stmt = stmt.where(Model.algorithm == algorithm)
    return list(session.scalars(stmt).all())


__all__ = ["get_model", "list_models", "record_run", "register_model"]
