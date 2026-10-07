"""Phase 5 feature engineering tests."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from app.features.cedear_technical import compute_cedear_technical_features
from app.features.core import get_feature_registry
from app.features.fx_features import compute_fx_features
from app.features.indicators import (
    atr_wilder,
    macd_and_signal,
    rolling_volatility,
    rsi_wilder,
)
from app.features.market_features import compute_market_features
from app.features.relative_features import compute_relative_features
from app.features.underlying_technical import compute_underlying_technical_features


@pytest.mark.unit
class TestFeatureRegistry:
    """Every family registers the features the models will consume."""

    def test_registry_has_cedear_technical(self) -> None:
        """The CEDEAR technical family exposes its headline features."""
        registry = get_feature_registry()
        names = [f.name for f in registry.list_family("cedear_technical")]
        assert "cedear_return_1d" in names
        assert "cedear_rsi_14" in names
        assert "cedear_macd" in names
        assert "cedear_rolling_volatility_20d" in names

    def test_registry_has_underlying_technical(self) -> None:
        """The underlying family mirrors the CEDEAR one."""
        registry = get_feature_registry()
        names = [f.name for f in registry.list_family("underlying_technical")]
        assert "underlying_return_1d" in names
        assert "underlying_rsi_14" in names

    def test_registry_has_market(self) -> None:
        """Market features cover BYMA and the placeholder US series."""
        registry = get_feature_registry()
        names = [f.name for f in registry.list_family("market")]
        assert "merval_return_1d" in names
        assert "sector_etf_return_1d" in names


@pytest.mark.unit
class TestFeatureStubs:
    """Without a session every family returns an honest all-None vector."""

    def test_compute_cedear_technical_without_session(self) -> None:
        """CEDEAR technicals degrade to nulls without a database session."""
        registry = get_feature_registry()
        result = compute_cedear_technical_features(registry, 1, datetime.now().astimezone())
        assert "cedear_return_1d" in result

    def test_compute_underlying_technical_without_session(self) -> None:
        """Underlying technicals degrade to nulls without a session."""
        registry = get_feature_registry()
        result = compute_underlying_technical_features(registry, 1, datetime.now().astimezone())
        assert "underlying_return_1d" in result

    def test_compute_fx_features_without_session(self) -> None:
        """FX features degrade to nulls without a session."""
        registry = get_feature_registry()
        result = compute_fx_features(registry, 1, datetime.now().astimezone())
        assert "fx_return_1d" in result

    def test_compute_relative_features_without_session(self) -> None:
        """Relative features degrade to nulls without a session."""
        registry = get_feature_registry()
        result = compute_relative_features(registry, 1, datetime.now().astimezone())
        assert "premium_discount" in result

    def test_compute_market_features_without_session(self) -> None:
        """Market features degrade to nulls without a session."""
        registry = get_feature_registry()
        result = compute_market_features(registry, 1, datetime.now().astimezone())
        assert "merval_return_1d" in result


@pytest.mark.unit
class TestIndicators:
    """Mathematical validation of the shared indicator implementations."""

    def test_rsi_bounds_on_mixed_series(self) -> None:
        """RSI on a mixed series stays inside [0, 100]."""
        closes = pd.Series([float(100 + (i % 5) - 2) for i in range(40)])
        value = rsi_wilder(closes, period=14)
        assert value is not None
        assert 0.0 <= value <= 100.0

    def test_rsi_all_gains_is_100(self) -> None:
        """A monotonically rising series has maximal RSI."""
        closes = pd.Series([float(100 + i) for i in range(20)])
        assert rsi_wilder(closes, period=14) == 100.0

    def test_rsi_flat_series_is_neutral(self) -> None:
        """A flat series is neither overbought nor oversold."""
        closes = pd.Series([100.0] * 20)
        assert rsi_wilder(closes, period=14) == 50.0

    def test_rsi_needs_full_window(self) -> None:
        """Two bars cannot produce a 14-period RSI."""
        assert rsi_wilder(pd.Series([100.0, 101.0]), period=14) is None

    def test_macd_signal_is_ema_of_line_not_close(self) -> None:
        """The signal line tracks the MACD line, not a close-price EMA."""
        closes = pd.Series([float(100 + i * 0.5 + (i % 3)) for i in range(60)])
        line, signal = macd_and_signal(closes)
        assert line is not None
        assert signal is not None
        # The signal must track the MACD line: recompute independently.
        expected_signal = (
            (closes.ewm(span=12, adjust=False).mean() - closes.ewm(span=26, adjust=False).mean())
            .ewm(span=9, adjust=False)
            .mean()
            .iloc[-1]
        )
        assert signal == pytest.approx(float(expected_signal))
        # And it must differ from the naive close-EMA proxy the old code used.
        naive_proxy = float(closes.ewm(span=9, adjust=False).mean().iloc[-1])
        assert signal != pytest.approx(naive_proxy)

    def test_macd_line_without_signal_when_short(self) -> None:
        """A short series yields a MACD line but no warmed-up signal."""
        closes = pd.Series([float(100 + i * 0.3) for i in range(28)])
        line, signal = macd_and_signal(closes)
        assert line is not None
        assert signal is None

    def test_macd_needs_slow_window(self) -> None:
        """Ten bars cannot produce a 26-period MACD."""
        line, signal = macd_and_signal(pd.Series([100.0] * 10))
        assert line is None
        assert signal is None

    def test_atr_is_positive_and_bounded(self) -> None:
        """ATR on a sane series is a small positive number."""
        n = 30
        highs = pd.Series([105.0 + (i % 4) for i in range(n)])
        lows = pd.Series([99.0 - (i % 3) for i in range(n)])
        closes = pd.Series([102.0 + (i % 5) - 2 for i in range(n)])
        value = atr_wilder(highs, lows, closes, period=14)
        assert value is not None
        assert value > 0.0
        assert value < 20.0

    def test_atr_needs_full_window(self) -> None:
        """A single bar cannot produce a 14-period ATR."""
        assert atr_wilder(pd.Series([105.0]), pd.Series([99.0]), pd.Series([102.0])) is None

    def test_rolling_volatility_non_negative(self) -> None:
        """Volatility is a non-negative annualised dispersion."""
        closes = pd.Series([float(100 + (i % 7) - 3) for i in range(40)])
        value = rolling_volatility(closes, window=20)
        assert value is not None
        assert value >= 0.0

    def test_rolling_volatility_needs_window(self) -> None:
        """Two bars cannot produce a 20-day volatility."""
        assert rolling_volatility(pd.Series([100.0, 101.0]), window=20) is None
