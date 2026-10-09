"""Phase 8 CEDEAR backtesting tests (no database).

The engine trades the CEDEAR in ARS with non-zero costs by default. The
exit criteria are tested directly: an always-long run matches buy-and-hold,
an always-flat run matches cash, and a strategy whose edge cannot cover
round-trip costs reports a loss.
"""

from __future__ import annotations

import numpy as np
import pytest
from numpy.typing import NDArray

from app.backtesting import (
    BacktestCosts,
    backtest_metrics,
    buy_and_hold_curve,
    cash_curve,
    run_backtest,
    signals_from_probas,
    to_usd_curve,
)


def _rising(n: int = 20, start: float = 100.0, step: float = 1.0) -> NDArray[np.float64]:
    """Steadily rising CEDEAR closes."""
    return (start + step * np.arange(n)).astype(np.float64)


@pytest.mark.unit
class TestCosts:
    """Costs are non-zero by default and validated on construction."""

    def test_defaults_are_non_zero(self) -> None:
        """A default cost model charges friction on every fill."""
        costs = BacktestCosts()
        assert costs.commission_rate > 0.0
        assert costs.slippage_rate > 0.0
        assert costs.round_trip_rate > 0.0

    def test_invalid_rates_raise(self) -> None:
        """Negative or >= 100 % rates are refused."""
        with pytest.raises(ValueError, match="commission_rate"):
            BacktestCosts(commission_rate=-0.01)
        with pytest.raises(ValueError, match="slippage_rate"):
            BacktestCosts(slippage_rate=1.0)

    def test_slippage_moves_against_the_trader(self) -> None:
        """Buys pay up, sells receive less."""
        costs = BacktestCosts(commission_rate=0.0, slippage_rate=0.01)
        assert costs.buy_execution_price(100.0) == pytest.approx(101.0)
        assert costs.sell_execution_price(100.0) == pytest.approx(99.0)


@pytest.mark.unit
class TestNullModels:
    """Phase 8 exit criterion: one-class signals reproduce the benchmarks."""

    def test_always_flat_equals_cash(self) -> None:
        """No signal means no trade, no cost, flat equity."""
        prices = _rising()
        result = run_backtest(prices, np.zeros(len(prices) - 1, dtype=np.int64))
        assert np.allclose(result.equity_curve, result.benchmark_cash)
        assert result.total_costs == pytest.approx(0.0)
        assert result.trades == []
        assert result.metrics["trade_count"] == pytest.approx(0.0)
        assert result.metrics["total_return"] == pytest.approx(0.0)

    def test_always_long_equals_buy_and_hold(self) -> None:
        """A permanent long buys once and holds like the benchmark."""
        prices = _rising()
        result = run_backtest(prices, np.ones(len(prices) - 1, dtype=np.int64))
        assert np.allclose(result.equity_curve, result.benchmark_buy_hold)
        assert result.metrics["total_return"] == pytest.approx(
            result.benchmark_metrics["total_return"]
        )
        # One entry, never closed: no round-trip trade is invented.
        assert result.trades == []

    def test_zero_cost_long_matches_frictionless_buy_and_hold(self) -> None:
        """With costs disabled the curve is the textbook buy-and-hold."""
        prices = np.array([100.0, 110.0, 121.0])
        costs = BacktestCosts(commission_rate=0.0, slippage_rate=0.0)
        result = run_backtest(
            prices, np.ones(2, dtype=np.int64), initial_capital=1000.0, costs=costs
        )
        assert result.equity_curve[-1] == pytest.approx(1210.0)
        assert result.total_costs == pytest.approx(0.0)


@pytest.mark.unit
class TestTransactionCosts:
    """Phase 8 exit criterion: costs turn a gross edge into a net loss."""

    def test_round_trip_costs_accumulate(self) -> None:
        """Each buy and each sell pays commission."""
        prices = np.array([100.0, 100.0, 100.0, 100.0])
        signals = np.array([1, 0, 1])
        result = run_backtest(prices, signals, initial_capital=10_000.0)
        assert result.total_costs > 0.0
        assert len(result.trades) == 1
        # The closed round trip plus the still-open second entry both paid.
        assert result.trades[0].costs < result.total_costs
        assert result.trades[0].costs > 0.0

    def test_churn_with_no_edge_loses(self) -> None:
        """Flipping every bar on a flat market bleeds commission."""
        prices = np.full(11, 100.0)
        signals = np.array([1, 0] * 5)
        result = run_backtest(prices, signals, initial_capital=10_000.0)
        assert result.equity_curve[-1] < 10_000.0
        assert result.metrics["total_return"] is not None
        assert result.metrics["total_return"] < 0.0

    def test_small_edge_cannot_cover_costs(self) -> None:
        """A +0.1 % move per trade is profit before costs, loss after."""
        prices = np.array([100.0, 100.1, 100.0, 100.1, 100.0])
        signals = np.array([1, 0, 1, 0])
        result = run_backtest(prices, signals, initial_capital=10_000.0)
        assert result.metrics["total_return"] is not None
        assert result.metrics["total_return"] < 0.0
        assert result.total_costs > 0.0

    def test_winning_trade_reports_positive_pnl(self) -> None:
        """A large favourable move survives costs with honest accounting."""
        prices = np.array([100.0, 100.0, 150.0, 150.0])
        signals = np.array([1, 1, 0])
        result = run_backtest(prices, signals, initial_capital=10_000.0)
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.pnl > 0.0
        assert result.metrics["win_rate"] == pytest.approx(1.0)
        assert result.metrics["profit_factor"] is None  # no losing trade to ratio against


@pytest.mark.unit
class TestBenchmarks:
    """Buy-and-hold, cash and the underlying-equivalent curves."""

    def test_buy_and_hold_pays_entry_costs(self) -> None:
        """The benchmark starts at cash, then holds fewer shares than frictionless."""
        prices = _rising(5)
        curve = buy_and_hold_curve(prices, initial_capital=10_000.0)
        assert curve[0] == pytest.approx(10_000.0)  # cash before the entry fill
        assert curve[1] < 10_000.0 * prices[1] / prices[0]  # entry friction priced in
        assert curve[-1] / curve[1] == pytest.approx(prices[-1] / prices[1])

    def test_cash_is_flat(self) -> None:
        """Cash never moves and never pays."""
        curve = cash_curve(5_000.0, 4)
        assert np.allclose(curve, 5_000.0)

    def test_strategy_can_be_compared_to_benchmarks(self) -> None:
        """A perfect-foresight run beats buy-and-hold on a mixed series."""
        prices = np.array([100.0, 110.0, 99.0, 109.0, 98.0, 108.0])
        # Long exactly the two up intervals: [100->110] and [99->109].
        signals = np.array([1, 0, 1, 0, 1])
        result = run_backtest(prices, signals, initial_capital=10_000.0)
        assert result.equity_curve[-1] > result.benchmark_buy_hold[-1]

    def test_underlying_benchmark_uses_same_maths(self) -> None:
        """The underlying-equivalent curve matches buy-and-hold on one series."""
        from app.backtesting.benchmarks import underlying_equivalent_curve

        prices = _rising(6)
        assert np.allclose(
            underlying_equivalent_curve(prices, initial_capital=10_000.0),
            buy_and_hold_curve(prices, initial_capital=10_000.0),
        )


@pytest.mark.unit
class TestMetrics:
    """Return, risk and trade statistics degrade honestly."""

    def test_flat_curve_has_no_volatility_or_sharpe(self) -> None:
        """A cash run reports None risk numbers, never 0.0 dressed as skill."""
        metrics = backtest_metrics(np.full(5, 10_000.0), trade_pnls=np.zeros(0), total_costs=0.0)
        assert metrics["total_return"] == pytest.approx(0.0)
        assert metrics["volatility"] is None
        assert metrics["sharpe"] is None
        assert metrics["win_rate"] is None
        assert metrics["max_drawdown"] == pytest.approx(0.0)

    def test_drawdown_and_profit_factor(self) -> None:
        """Peak-to-trough and gross-profit/gross-loss match hand computation."""
        metrics = backtest_metrics(
            np.array([100.0, 120.0, 90.0, 110.0]),
            trade_pnls=np.array([50.0, -20.0, -10.0]),
            total_costs=5.0,
        )
        assert metrics["max_drawdown"] == pytest.approx(30.0 / 120.0)
        assert metrics["win_rate"] == pytest.approx(1 / 3)
        assert metrics["trade_count"] == pytest.approx(3.0)
        assert metrics["average_trade"] == pytest.approx(20.0 / 3)
        assert metrics["profit_factor"] == pytest.approx(50.0 / 30.0)
        assert metrics["total_costs"] == pytest.approx(5.0)

    def test_too_little_data_raises(self) -> None:
        """A single equity point cannot be scored."""
        with pytest.raises(ValueError, match="at least 2"):
            backtest_metrics(np.array([100.0]), trade_pnls=np.zeros(0), total_costs=0.0)

    def test_usd_curve_needs_positive_fx(self) -> None:
        """Zero or negative FX is refused, never divided through."""
        with pytest.raises(ValueError, match="positive"):
            to_usd_curve(np.array([100.0, 110.0]), np.array([1000.0, 0.0]))


@pytest.mark.unit
class TestArsUsdSeparation:
    """ARS and USD-adjusted returns are reported separately, never mixed."""

    def test_fx_series_adds_usd_metrics(self) -> None:
        """With FX the run carries both accountings side by side."""
        prices = _rising(10)
        fx = np.linspace(900.0, 1000.0, 10)
        result = run_backtest(
            prices,
            np.ones(9, dtype=np.int64),
            initial_capital=1_000_000.0,
            fx_ars_per_usd=fx,
        )
        assert result.metrics_usd is not None
        assert result.benchmark_metrics_usd is not None
        # ARS gains while the peso weakens faster: USD total return differs.
        assert result.metrics["total_return"] != pytest.approx(result.metrics_usd["total_return"])

    def test_without_fx_there_are_no_usd_metrics(self) -> None:
        """No FX series means no USD numbers are invented."""
        result = run_backtest(_rising(6), np.ones(5, dtype=np.int64))
        assert result.metrics_usd is None
        assert result.benchmark_metrics_usd is None

    def test_misaligned_fx_is_refused(self) -> None:
        """An FX series that does not cover every bar is rejected."""
        with pytest.raises(ValueError, match="align"):
            run_backtest(_rising(6), np.ones(5, dtype=np.int64), fx_ars_per_usd=np.ones(4))


@pytest.mark.unit
class TestHoldingPeriod:
    """Epoch holding generalises the session-by-session simulation."""

    def test_epoch_boundaries_are_the_only_decision_points(self) -> None:
        """A [1, 0] pair over 5-session epochs buys at 0 and sells at 5."""
        prices = _rising(11, start=100.0, step=1.0)
        result = run_backtest(prices, np.array([1, 0]), holding_period=5, initial_capital=10_000.0)
        assert result.positions.tolist() == [1] * 5 + [0] * 5
        assert len(result.trades) == 1
        assert result.trades[0].entry_index == 0
        assert result.trades[0].exit_index == 5
        # Mid-epoch bars only mark to market: equity tracks the rising price.
        assert result.equity_curve[3] > result.equity_curve[1]

    def test_holding_one_reproduces_bar_by_bar(self) -> None:
        """holding_period=1 is exactly the per-interval simulation."""
        prices = _rising(6)
        signals = np.array([1, 0, 1, 1, 0])
        default = run_backtest(prices, signals)
        explicit = run_backtest(prices, signals, holding_period=1)
        assert np.allclose(default.equity_curve, explicit.equity_curve)
        assert default.total_costs == pytest.approx(explicit.total_costs)

    def test_always_long_epoch_matches_buy_and_hold(self) -> None:
        """The null-model criterion holds for longer epochs too."""
        prices = _rising(21)
        result = run_backtest(prices, np.ones(4, dtype=np.int64), holding_period=5)
        assert np.allclose(result.equity_curve, result.benchmark_buy_hold)


@pytest.mark.unit
class TestValidation:
    """Malformed inputs are refused before any simulation."""

    def test_signal_length_must_match_epochs(self) -> None:
        """Five prices hold four intervals: four 1-bar epochs, or two 2-bar ones."""
        with pytest.raises(ValueError, match="one signal per"):
            run_backtest(_rising(5), np.ones(5, dtype=np.int64))
        with pytest.raises(ValueError, match="one signal per"):
            run_backtest(_rising(5), np.ones(3, dtype=np.int64), holding_period=2)
        with pytest.raises(ValueError, match="holding_period"):
            run_backtest(_rising(5), np.ones(4, dtype=np.int64), holding_period=0)

    def test_signals_must_be_binary(self) -> None:
        """A signal of 2 is not a position."""
        with pytest.raises(ValueError, match="binary"):
            run_backtest(_rising(4), np.array([0, 2, 1]))

    def test_non_positive_price_is_refused(self) -> None:
        """A zero close cannot size a position."""
        with pytest.raises(ValueError, match="positive"):
            run_backtest(np.array([100.0, 0.0, 100.0]), np.array([1, 0]))

    def test_proba_thresholding(self) -> None:
        """P(up) maps to long/flat at the threshold."""
        assert signals_from_probas(np.array([0.2, 0.5, 0.9])).tolist() == [0, 1, 1]
        with pytest.raises(ValueError, match="threshold"):
            signals_from_probas(np.array([0.5]), threshold=1.5)
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            signals_from_probas(np.array([1.5]))
