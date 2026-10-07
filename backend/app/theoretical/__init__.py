"""Theoretical CEDEAR price engine.

Computes: theoretical = underlying_price * fx / ratio

Stores full provenance (ratio_used, fx_used, underlying_price_used, market dates)
so any historical value can be re-derived and audited.
"""

from app.theoretical.engine import (
    TheoreticalInputs,
    TheoreticalResult,
    build_theoretical_result,
    compute_premium_discount,
    compute_theoretical_price,
    resolve_theoretical_inputs,
)

__all__ = [
    "TheoreticalInputs",
    "TheoreticalResult",
    "build_theoretical_result",
    "compute_premium_discount",
    "compute_theoretical_price",
    "resolve_theoretical_inputs",
]
