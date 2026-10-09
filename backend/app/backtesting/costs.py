"""Transaction costs and slippage for CEDEAR backtests.

Both rates are proportional to the traded notional and **non-zero by
default**: a published result must never assume frictionless trading
(methodology section 7). The cost model is deliberately simple - one
commission rate plus one slippage rate, charged on every fill - so the
null-model tests (always-long equals buy-and-hold, always-flat equals
cash) hold exactly.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Default broker commission per fill (0.5 % of the traded notional).
DEFAULT_COMMISSION_RATE = 0.005
#: Default slippage per fill (0.1 % adverse move on the execution price).
DEFAULT_SLIPPAGE_RATE = 0.001

#: 252 BYMA sessions per year, used to annualise returns.
TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True, slots=True)
class BacktestCosts:
    """Proportional cost model, charged on every buy and every sell.

    Attributes:
        commission_rate: Fraction of the traded notional paid as commission
            (e.g. ``0.005`` = 0.5 % per fill).
        slippage_rate: Adverse fraction applied to the execution price
            (buys pay ``price * (1 + slippage)``, sells receive
            ``price * (1 - slippage)``).
    """

    commission_rate: float = DEFAULT_COMMISSION_RATE
    slippage_rate: float = DEFAULT_SLIPPAGE_RATE

    def __post_init__(self) -> None:
        """Reject negative or >= 100 % rates."""
        for name in ("commission_rate", "slippage_rate"):
            rate = getattr(self, name)
            if not 0.0 <= rate < 1.0:
                raise ValueError(f"{name} must be in [0, 1), got {rate!r}")

    @property
    def round_trip_rate(self) -> float:
        """Approximate round-trip friction (commission both ways + slippage)."""
        return 2.0 * (self.commission_rate + self.slippage_rate)

    def buy_execution_price(self, close: float) -> float:
        """Execution price for a buy at ``close`` (slippage moves against us)."""
        return close * (1.0 + self.slippage_rate)

    def sell_execution_price(self, close: float) -> float:
        """Execution price for a sell at ``close`` (slippage moves against us)."""
        return close * (1.0 - self.slippage_rate)

    def commission_on(self, notional: float) -> float:
        """Commission owed on a fill of ``notional`` ARS."""
        return notional * self.commission_rate


__all__ = [
    "DEFAULT_COMMISSION_RATE",
    "DEFAULT_SLIPPAGE_RATE",
    "TRADING_DAYS_PER_YEAR",
    "BacktestCosts",
]
