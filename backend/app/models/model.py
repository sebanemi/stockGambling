"""Model registry database models.

``sg_models`` identifies a trainable algorithm configuration (name, algorithm,
feature version, **horizon**, hyperparameters, artifact location).
``sg_model_runs`` records each training event: the chronological training
window, the sample size and the resulting metrics. Walk-forward evaluation
(Phase 7) adds one run per fold; the test period is evaluated exactly once
per model.

One row serves exactly one horizon: ``("m1", "1d")`` and ``("m1", "1w")``
are different artifacts trained on different labels.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Model(Base, TimestampMixin):
    """One registered model configuration for one horizon.

    The row is the identity; the fitted parameters live in the artifact file
    (joblib) at ``artifact_path``. Two rows never share ``(name, horizon)``.
    """

    __tablename__ = "sg_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0")
    horizon: Mapped[str] = mapped_column(
        String(8), nullable=False, default="1d", server_default=text("'1d'")
    )
    params: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    artifact_path: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    runs: Mapped[list[ModelRun]] = relationship(back_populates="model")

    __table_args__ = (
        UniqueConstraint("name", "horizon", name="uq_sg_models_name_horizon"),
        Index("ix_sg_models_algorithm", "algorithm"),
        Index("ix_sg_models_horizon", "horizon"),
    )


class ModelRun(Base, TimestampMixin):
    """One training event for a registered model."""

    __tablename__ = "sg_model_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("sg_models.id", ondelete="CASCADE"), nullable=False
    )
    train_start: Mapped[date] = mapped_column(Date, nullable=False)
    train_end: Mapped[date] = mapped_column(Date, nullable=False)
    n_train: Mapped[int] = mapped_column(Integer, nullable=False)
    n_features: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="succeeded")
    notes: Mapped[str | None] = mapped_column(String(512))

    model: Mapped[Model] = relationship(back_populates="runs")

    __table_args__ = (Index("ix_sg_model_runs_model_id", "model_id"),)


__all__ = ["Model", "ModelRun"]
