"""Next-session direction prediction API.

``GET /api/v1/cedears/{symbol}/prediction`` serves a calibrated probability
about the CEDEAR close over a requested horizon - never a certainty, never
investment advice. Every value traces to stored data:

* the feature vector is computed from stored bars at the requested
  prediction instant (same builder the worker persists);
* the model is the registered ``(name, horizon)`` configuration whose fitted
  bytes live in the artifact file training wrote - the API never fits, tunes
  or defaults, and never serves a 1d model for a 1w question;
* ``probability_up + probability_down == 1`` by construction (the model's
  two-column envelope, verified before responding).

Refusals are explicit: unknown symbol (``404``), unknown model/horizon
(``404``), model without a readable artifact (``409``), feature-width
mismatch against the fitted model (``409``), malformed ``as_of`` or unknown
horizon (``422``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

import numpy as np
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.api.deps import SessionDep
from app.api.v1.common import resolve_instrument
from app.core.logging import get_logger
from app.core.time import ensure_utc, to_market_date, utc_now
from app.domain.vocabulary import format_ratio
from app.features.build import compute_all_features, feature_version
from app.modeling.baselines import ALGORITHMS
from app.modeling.horizons import DEFAULT_HORIZON, HORIZONS, describe_horizon, horizon_sessions
from app.modeling.registry import get_model
from app.models.instrument import InstrumentRatioHistory
from app.models.market_data import LocalPriceBar

router = APIRouter(prefix="/cedears", tags=["predictions"])

logger = get_logger(__name__)


class PredictionModelInfo(BaseModel):
    """The registered model that produced the probabilities."""

    name: str
    algorithm: str
    feature_version: str = Field(description="Feature version the model was trained with")
    horizon: str = Field(description="Forecast horizon the model was trained for")


class PredictionResponse(BaseModel):
    """Direction probabilities over the requested horizon, with provenance."""

    symbol: str
    as_of: str = Field(description="Prediction instant (aware UTC ISO-8601)")
    market_date: str = Field(description="BYMA date the instant falls on")
    horizon: str = Field(description="Forecast horizon served")
    horizon_detail: str = Field(description="What the horizon means in sessions")
    model: PredictionModelInfo
    feature_version: str = Field(description="Feature version computed at this instant")
    feature_version_match: bool = Field(
        description="Whether the live feature version matches the training one"
    )
    probability_up: float = Field(description="P(CEDEAR close rises over the horizon)")
    probability_down: float = Field(description="P(CEDEAR close does not rise over the horizon)")
    actual_close: Decimal | None = Field(
        default=None,
        description="Latest stored CEDEAR close on or before the market date, if any",
    )
    current_ratio: Decimal | None = Field(
        default=None, description="Conversion ratio in force on the market date, if known"
    )
    current_ratio_formatted: str | None = None


def _parse_instant(raw: str | None) -> datetime:
    """Parse the requested prediction instant or default to now.

    Raises:
        HTTPException: ``422`` when ``raw`` is not an aware ISO-8601 instant.
    """
    if raw is None:
        return utc_now()
    try:
        return ensure_utc(datetime.fromisoformat(raw))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "invalid_as_of",
                "as_of": raw,
                "message": "as_of must be an aware ISO-8601 instant.",
            },
        ) from exc


@router.get(
    "/{symbol}/prediction", response_model=PredictionResponse, summary="Get direction prediction"
)
def get_prediction(
    session: SessionDep,
    symbol: str,
    model: Annotated[str, Query(description="Registered model name.")] = "",
    horizon: Annotated[str, Query(description="Forecast horizon.")] = DEFAULT_HORIZON,
    as_of: Annotated[
        str | None,
        Query(description="Prediction instant (aware ISO-8601). Defaults to now."),
    ] = None,
) -> PredictionResponse:
    """Serve direction probabilities over the requested horizon for one CEDEAR.

    Raises:
        HTTPException: ``404`` for an unknown symbol or model/horizon,
            ``409`` when the model has no readable artifact or its fitted
            width disagrees with the live feature vector, ``422`` for a
            malformed ``as_of`` or an unknown horizon.
    """
    instrument = resolve_instrument(session, symbol)
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
    name = model.strip()
    row = get_model(session, name, horizon) if name else None
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "model_not_found",
                "model": name,
                "horizon": horizon,
                "message": (
                    f"Model {name!r} is not registered for horizon {horizon!r}."
                    if name
                    else "A registered model name is required (?model=<name>)."
                ),
            },
        )
    if not row.artifact_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "model_not_trained",
                "model": row.name,
                "message": f"Model {row.name!r} has no artifact: it was never trained.",
            },
        )
    algorithm = ALGORITHMS.get(row.algorithm)
    if algorithm is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "model_algorithm_unknown",
                "model": row.name,
                "message": f"Model {row.name!r} uses unknown algorithm {row.algorithm!r}.",
            },
        )
    try:
        loaded = algorithm.load(row.artifact_path)
    except Exception as exc:
        logger.warning("prediction.artifact_unreadable", model=row.name, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "model_artifact_unreadable",
                "model": row.name,
                "message": f"Model {row.name!r} has an unreadable artifact.",
            },
        ) from exc

    instant = _parse_instant(as_of)
    live_version = feature_version()
    values = compute_all_features(session, instrument.id, instant)
    live: dict[str, float] = {
        key: (float(raw) if raw is not None else float("nan")) for key, raw in values.items()
    }
    # Column order is a silent-prediction-breaker for learned models: prefer
    # the exact training order stored in the model params, and fall back to
    # sorted keys only when the sets disagree (old rows predate the field).
    stored = row.params.get("feature_names") if isinstance(row.params, dict) else None
    if (
        isinstance(stored, list)
        and stored
        and all(isinstance(name, str) for name in stored)
        and set(stored) == set(live)
    ):
        ordered_names = [str(name) for name in stored]
    else:
        ordered_names = sorted(live)
    matrix = np.array([[live[key] for key in ordered_names]], dtype=np.float64)
    expected = getattr(loaded, "n_features_", None)
    if expected is not None and int(expected) != matrix.shape[1]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "feature_mismatch",
                "model": row.name,
                "message": (
                    f"Model {row.name!r} was fitted on {int(expected)} features but the "
                    f"live vector has {matrix.shape[1]}; refusing to silently reshape."
                ),
            },
        )
    try:
        proba = np.asarray(loaded.predict_proba(matrix), dtype=np.float64).reshape(-1)
    except Exception as exc:
        logger.warning("prediction.predict_failed", model=row.name, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "model_not_usable",
                "model": row.name,
                "message": f"Model {row.name!r} refused to predict on the live vector.",
            },
        ) from exc
    if proba.shape != (2,) or not bool(np.all(np.isfinite(proba))):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "model_contract_violation",
                "model": row.name,
                "message": "The model broke the [P(down), P(up)] probability contract.",
            },
        )
    if abs(float(proba.sum()) - 1.0) > 1e-6:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "model_contract_violation",
                "model": row.name,
                "message": "The model probabilities do not sum to 1.",
            },
        )

    market_day = to_market_date(instant)
    latest_bar = session.scalar(
        select(LocalPriceBar)
        .where(
            LocalPriceBar.instrument_id == instrument.id,
            LocalPriceBar.market_date <= market_day,
        )
        .order_by(LocalPriceBar.market_date.desc())
        .limit(1)
    )
    period = session.scalar(
        select(InstrumentRatioHistory)
        .where(
            InstrumentRatioHistory.instrument_id == instrument.id,
            InstrumentRatioHistory.effective_from <= market_day,
            or_(
                InstrumentRatioHistory.effective_to.is_(None),
                InstrumentRatioHistory.effective_to > market_day,
            ),
        )
        .order_by(InstrumentRatioHistory.effective_from.desc())
    )
    return PredictionResponse(
        symbol=instrument.symbol,
        as_of=instant.isoformat(),
        market_date=market_day.isoformat(),
        horizon=horizon,
        horizon_detail=describe_horizon(horizon),
        model=PredictionModelInfo(
            name=row.name,
            algorithm=row.algorithm,
            feature_version=row.feature_version,
            horizon=row.horizon,
        ),
        feature_version=live_version,
        feature_version_match=(live_version == row.feature_version),
        probability_up=float(proba[1]),
        probability_down=float(proba[0]),
        actual_close=None if latest_bar is None else latest_bar.close,
        current_ratio=None if period is None else period.ratio,
        current_ratio_formatted=None if period is None else format_ratio(period.ratio),
    )


__all__ = ["PredictionModelInfo", "PredictionResponse", "router"]
