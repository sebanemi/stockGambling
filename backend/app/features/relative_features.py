"""Relative feature definitions and computation.

Premium/discount, expected/actual CEDEAR return, local divergence.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.core import FeatureDefinition, FeatureRegistry
from app.models.market_data import FxRate, LocalPriceBar, UnderlyingPriceBar
from app.models.theoretical import TheoreticalPriceBar


def register_relative_features(registry: FeatureRegistry) -> None:
    """Register relative feature definitions."""
    family = "relative"
    features = [
        FeatureDefinition(
            name="premium_discount",
            family=family,
            description="(local_price - theoretical_price) / theoretical_price",
            inputs=["local_price_bar", "theoretical_price_bar"],
            lookback_days=1,
        ),
        FeatureDefinition(
            name="expected_cedear_return",
            family=family,
            description="Expected CEDEAR return from underlying + FX",
            inputs=["underlying_price_bar", "fx_rate"],
            lookback_days=1,
        ),
        FeatureDefinition(
            name="actual_cedear_return",
            family=family,
            description="Actual CEDEAR return (local market)",
            inputs=["local_price_bar"],
            lookback_days=1,
        ),
        FeatureDefinition(
            name="local_underlying_divergence",
            family=family,
            description="Actual CEDEAR return - expected CEDEAR return",
            inputs=["local_price_bar", "underlying_price_bar", "fx_rate"],
            lookback_days=1,
        ),
    ]
    for f in features:
        registry.register(f)


def compute_relative_features(
    registry: FeatureRegistry,
    instrument_id: int,
    as_of: datetime,
    session: Session | None = None,
) -> dict[str, Decimal | float | None]:
    """Compute relative features for one instrument at `as_of`.

    As-of rule (consistent with :mod:`app.alignment.asof`):

    * ``as_of`` is the BYMA-close prediction instant for date ``D``.
    * Local CEDEAR bars may use ``D`` itself (own-market close is known at
      the instant) plus the previous session.
    * Underlying bars are lagged one completed session: the latest eligible
      underlying date is strictly before ``D``. Same-day underlying closes
      are never visible here.
    * FX has no lag: the latest observation on or before ``D`` is eligible.
    * ``expected_cedear_return`` compounds multiplicatively,
      ``(1 + r_u) * (1 + r_fx) - 1``, because the theoretical identity is
      multiplicative (``underlying * fx / ratio``), not additive.
    """
    default_none: dict[str, Decimal | float | None] = {
        "premium_discount": None,
        "expected_cedear_return": None,
        "actual_cedear_return": None,
        "local_underlying_divergence": None,
    }
    if session is None:
        return default_none
    from app.core.time import to_market_date

    market_date = to_market_date(as_of)

    # Local: latest session on or before D (own-market close is known at T).
    local_bar = session.scalar(
        select(LocalPriceBar)
        .where(
            LocalPriceBar.instrument_id == instrument_id,
            LocalPriceBar.market_date <= market_date,
        )
        .order_by(LocalPriceBar.market_date.desc())
        .limit(1)
    )
    prev_local_bar = (
        session.scalar(
            select(LocalPriceBar)
            .where(
                LocalPriceBar.instrument_id == instrument_id,
                LocalPriceBar.market_date < local_bar.market_date,
            )
            .order_by(LocalPriceBar.market_date.desc())
            .limit(1)
        )
        if local_bar is not None
        else None
    )
    # Theoretical: latest stored value on or before D (never computed here).
    theoretical_bar = session.scalar(
        select(TheoreticalPriceBar)
        .where(
            TheoreticalPriceBar.instrument_id == instrument_id,
            TheoreticalPriceBar.market_date <= market_date,
        )
        .order_by(TheoreticalPriceBar.market_date.desc())
        .limit(1)
    )
    # Underlying: one-session lag - latest session strictly before D.
    underlying_bar = session.scalar(
        select(UnderlyingPriceBar)
        .where(
            UnderlyingPriceBar.instrument_id == instrument_id,
            UnderlyingPriceBar.market_date < market_date,
        )
        .order_by(UnderlyingPriceBar.market_date.desc())
        .limit(1)
    )
    prev_underlying_bar = (
        session.scalar(
            select(UnderlyingPriceBar)
            .where(
                UnderlyingPriceBar.instrument_id == instrument_id,
                UnderlyingPriceBar.market_date < underlying_bar.market_date,
            )
            .order_by(UnderlyingPriceBar.market_date.desc())
            .limit(1)
        )
        if underlying_bar is not None
        else None
    )
    # FX: no lag - latest observation on or before D.
    fx_bar = session.scalar(
        select(FxRate)
        .where(
            FxRate.pair == "USDARS",
            FxRate.market_date <= market_date,
        )
        .order_by(FxRate.market_date.desc())
        .limit(1)
    )
    prev_fx_bar = (
        session.scalar(
            select(FxRate)
            .where(
                FxRate.pair == "USDARS",
                FxRate.market_date < fx_bar.market_date,
            )
            .order_by(FxRate.market_date.desc())
            .limit(1)
        )
        if fx_bar is not None
        else None
    )
    results = default_none.copy()

    # Actual CEDEAR return (own-market sessions D vs D-1).
    if local_bar is not None and prev_local_bar is not None:
        if (
            prev_local_bar.close is not None
            and local_bar.close is not None
            and prev_local_bar.close != 0
        ):
            results["actual_cedear_return"] = float(
                (local_bar.close - prev_local_bar.close) / prev_local_bar.close
            )
        else:
            results["actual_cedear_return"] = None
    else:
        results["actual_cedear_return"] = None

    # Expected CEDEAR return, compounded: (1 + r_u) * (1 + r_fx) - 1.
    if (
        underlying_bar is not None
        and fx_bar is not None
        and prev_underlying_bar is not None
        and prev_fx_bar is not None
    ):
        prev_u_close = prev_underlying_bar.close
        u_close = underlying_bar.close
        underlying_return: float | None = None
        if prev_u_close is not None and u_close is not None and prev_u_close != 0:
            underlying_return = float((u_close - prev_u_close) / prev_u_close)
        prev_fx_close = prev_fx_bar.close
        fx_close = fx_bar.close
        fx_return: float | None = None
        if prev_fx_close is not None and fx_close is not None and prev_fx_close != 0:
            fx_return = float((fx_close - prev_fx_close) / prev_fx_close)
        if underlying_return is not None and fx_return is not None:
            results["expected_cedear_return"] = float(
                (1.0 + underlying_return) * (1.0 + fx_return) - 1.0
            )
        else:
            results["expected_cedear_return"] = None
    else:
        results["expected_cedear_return"] = None

    # Premium / discount from theoretical price
    if theoretical_bar is not None and local_bar is not None:
        if theoretical_bar.theoretical_price is not None and local_bar.close is not None:
            theoretical_price = float(theoretical_bar.theoretical_price)
            local_price = float(local_bar.close)
            if theoretical_price != 0:
                results["premium_discount"] = float(
                    (local_price - theoretical_price) / theoretical_price
                )
            else:
                results["premium_discount"] = None
        else:
            results["premium_discount"] = None
    else:
        results["premium_discount"] = None

    # Local / underlying divergence
    actual = results["actual_cedear_return"]
    expected = results["expected_cedear_return"]
    if actual is not None and expected is not None:
        results["local_underlying_divergence"] = float(float(actual) - float(expected))
    else:
        results["local_underlying_divergence"] = None
    return results
