"""Forecast horizons for CEDEAR direction models.

The platform predicts the direction of the CEDEAR close over a fixed
session horizon ``H``:

* ``y(t) = 1`` when ``close(t+H) > close(t)`` over consecutive stored
  sessions (flat counts as down, exactly like the 1d target);
* horizons count **sessions**, never calendar days - weekends and holidays
  carry no information and must not stretch or shrink a horizon.

One model row serves exactly one horizon (see ``Model.horizon``): a 1d
model and a 1m model are different artifacts trained on different labels,
and the prediction API refuses to serve one for the other. Longer horizons
are honest but weak - the dashboard frames every horizon as a probability,
and walk-forward metrics will show long horizons near the coin flip.
"""

from __future__ import annotations

#: Horizon name to sessions ahead. Week/month/year are session counts
#: (5/21/252 trading days), the same convention index providers use.
HORIZONS: dict[str, int] = {
    "1d": 1,
    "1w": 5,
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "1y": 252,
    "2y": 504,
}

#: The default horizon everywhere (registry, prediction API, dashboard).
DEFAULT_HORIZON = "1d"


def horizon_sessions(horizon: str) -> int:
    """Return the session count for a horizon name.

    Raises:
        ValueError: When ``horizon`` is not a served horizon.
    """
    try:
        return HORIZONS[horizon]
    except KeyError:
        raise ValueError(f"Unknown horizon {horizon!r}. Served: {sorted(HORIZONS)}") from None


def describe_horizon(horizon: str) -> str:
    """Human description of a horizon (sessions + calendar gloss)."""
    sessions = horizon_sessions(horizon)
    gloss = {
        "1d": "next session",
        "1w": "next 5 sessions (~1 week)",
        "1m": "next 21 sessions (~1 month)",
        "3m": "next 63 sessions (~3 months)",
        "6m": "next 126 sessions (~6 months)",
        "1y": "next 252 sessions (~1 year)",
        "2y": "next 504 sessions (~2 years)",
    }[horizon]
    return f"{horizon}: {gloss} ({sessions} sessions ahead)"


__all__ = ["DEFAULT_HORIZON", "HORIZONS", "describe_horizon", "horizon_sessions"]
