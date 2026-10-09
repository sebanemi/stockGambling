# Methodology

This document states in advance **how** StockGambling will be built and evaluated, so that
the rules cannot be relaxed after the results are known.

---

## 1. Prediction target

The target is **direction**, not price, over an explicit session horizon `H`:

```
y(t) = 1  if  CEDEAR_Close(t+H) > CEDEAR_Close(t)
y(t) = 0  otherwise
```

* `t` and `t+H` are **stored sessions** `H` apart, counted in sessions, never
  calendar days. Weekends and holidays carry no information.
* Served horizons: `1d` (1), `1w` (5), `1m` (21), `3m` (63), `6m` (126),
  `1y` (252), `2y` (504) sessions.
* Horizon is explicit everywhere: registry entry (`sg_models.horizon`), model
  artifact name, prediction payload, and backtest holding period. One row
  serves exactly one horizon; a 1d model never answers a 1w question.
* The prediction is made **at the BYMA close of `t`**, using only information
  available at that instant.

### Ties

`>` (strictly greater) defines the positive class. A flat window is a `0`.
Class imbalance must be reported, not hidden - including the degenerate case
below.

### Degenerate windows (learned the hard way, October 2026)

On real data, long-horizon test windows in a sustained trend are
single-class (e.g. 126/126 up). A model predicting the majority then scores a
perfect 1.0 that is the trend, not skill - and the majority baseline scores
it too. Every run therefore records `test_up_rate` and the walk-forward
aggregate (`wf_balanced_accuracy`) next to the single-window score, and the
dashboard flags one-sided test windows as trend, not skill.

---

## 2. The three things that are not the same

The project keeps three ideas strictly apart, because conflating them is the most common
way a CEDEAR model looks brilliant and is worthless.

| Concept                          | What it is                                                          |
| -------------------------------- | ------------------------------------------------------------------- |
| **Underlying prediction**        | A forecast about the foreign security. Not a CEDEAR forecast.       |
| **Theoretical CEDEAR value**     | `underlying_price x fx / ratio` - an accounting identity, not a quote. |
| **Actual CEDEAR prediction**     | A calibrated probability about the BYMA close of the CEDEAR.         |

An "AAPL will rise" forecast on NASDAQ does **not** imply the AAPL CEDEAR rises by the
same percentage: the ratio, USD/ARS, the local session, local liquidity and the premium
or discount at which the CEDEAR trades all intervene.

```
expected_cedear_return ~= underlying_return + fx_return
actual_cedear_return  =  expected_cedear_return + local_underlying_divergence
```

`local_underlying_divergence` is itself a feature. That is the point.

---

## 3. Theoretical value and premium/discount

```
theoretical_CEDEAR_value(t) = underlying_price(t) * fx(t) / ratio(t)
premium_discount(t)         = (actual(t) - theoretical(t)) / theoretical(t)
```

`ratio(t)` is the ratio **valid on date `t`** from `instrument_ratio_history`, never the
current one. Every computed row stores `ratio_used`, `fx_used`, `underlying_price_used`
and both market timestamps, so any historical value can be re-derived and audited.

The theoretical value is an identity, not a quote. Its residual against the market price
is the local premium/discount, and that residual carries real information (local
sentiment, capital controls, repatriation frictions, local liquidity).

---

## 4. Features

Grouped so that each family can be ablated, which is how causal claims get tested.

**CEDEAR technical** - 1/5/10/20d returns, SMA 5/10/20/50, EMA 10/20, RSI 14, MACD and
signal, ATR, rolling volatility, volume change, volume / rolling average volume.

**Underlying** - the same family computed on the underlying, so the model can tell local
behaviour from foreign behaviour.

**FX** - USD/ARS returns over 1/5/20d, volatility, momentum, rolling change. The series is
configurable; the official exchange rate is not assumed to be the only relevant reference.

**Relative** - `premium_discount`, `expected_cedear_return`, `actual_cedear_return`,
`local_underlying_divergence`.

**Market** - S&P 500, Nasdaq, Dow, Russell 2000, VIX, sector ETF, BYMA indexes. Read
through a generic `market_data` reader so adding one never changes the model interface.

**Local market** - Merval returns, CEDEAR aggregate volume, local volatility, local
activity. All timestamped in Argentine local time.

Every feature declares: definition, inputs, lookback, and the `as_of` instant it is valid
at. Fundamentals and news are deliberately excluded from the MVP, and when added they must
be keyed by **availability date**, never by fiscal period or publication time.

---

## 5. Leakage prevention

Forbidden inputs at prediction time `T`:

* underlying, FX, CEDEAR or volume observations at or after `T`
* future conversion ratios (the ratio is resolved *as of the observation date*)
* news or fundamentals published after `T`, or revised afterwards
* normalisations, scalers or imputers fitted on the full dataset
* shuffled time series, random K-fold, or any split that puts later data in training
* a market date derived from UTC instead of the market's own timezone

Enforcement, in order of strength:

1. **Structural** - `app/core/time.py` refuses naive datetimes; the as-of rule lives in one
   module; the ORM stores `TIMESTAMPTZ` plus explicit market dates.
2. **Test suite** - a dedicated `leakage` marker (`pytest -m leakage`) with a test per
   leakage source. CI must pass it.
3. **Protocol** - the test period is evaluated once, and no model, threshold or feature
   set is selected using it.

---

## 6. Validation protocol

Chronological folds only. Walk-forward expanding windows, with the exact boundaries
derived from the available CEDEAR history rather than hardcoded:

```
train 2019-2022  ->  validate 2023
train 2019-2023  ->  validate 2024
train 2019-2024  ->  test     2025   (evaluated once)
```

* `train_test_split(shuffle=True)` is banned; `k`-fold on time series is banned.
* Scalers and imputers are fitted **inside each fold**, on training data only.
* Hyperparameters are chosen on validation folds. The test fold is opened once.
* Fold definitions, feature version and data period are written to the experiment record
  so the run is reproducible.
* Fold geometry scales with the horizon (`app/train.py::fold_geometry`):
  the test window holds at least one full horizon (`max(63, H)` sessions) and
  training at least four (`max(252, 4H)`), so every window means the same thing
  at every horizon. The serving artifact fits on everything before the most
  recent window; that window is evaluated once.

### Metrics

Classification: accuracy, **balanced accuracy**, precision, recall, F1, ROC-AUC, log
loss, Brier score, and the majority-class baseline alongside every result. A model that
cannot beat the majority baseline on balanced accuracy is reported as such.

Accuracy alone is never sufficient. A CEDEAR series is close to a random walk, so a high
accuracy usually means leakage rather than skill.

---

## 7. Backtesting

The strategy trades the **CEDEAR in ARS**.

Included: initial capital, position sizing, entry and exit prices, available cash,
portfolio value, **transaction costs** and **slippage** (configurable, non-zero by
default), and the resulting equity curve.

Signals are per holding-period epoch: a signal decided at an epoch boundary
is held for `H` sessions, so a 1w model trades the 5-session view it was
trained on. `H = 1` is the session-by-session simulation. Epoch boundaries
are the only decision points; mid-epoch bars only mark to market.

Benchmarks, always reported:

1. Buy and hold the CEDEAR.
2. Cash / no trade.
3. Optional: the underlying-equivalent position (the same notional in USD).

Metrics: total return, annualised return, volatility, Sharpe ratio, maximum drawdown, win
rate, trade count, average trade, profit factor, total transaction costs, benchmark
return.

**ARS and USD-adjusted returns are reported separately and never mixed.**

### Sanity gates

* A model that always predicts one class must produce results indistinguishable from the
  benchmarks. If it does not, the backtester is broken.
* A strategy whose gross edge is smaller than round-trip costs must show a loss.
* Transaction costs and slippage are not allowed to default to zero in a published result.

---

## 8. Scientific integrity

* No fabricated performance numbers. If a number is not measured, it is not reported.
* No future information, ever.
* The test period is never used to choose anything.
* Accuracy alone never justifies a claim of profitability.
* Suspiciously good results are treated as a bug report. Investigate: leakage, timezone
  errors, ratio errors, FX errors, duplicated data, survivorship bias, missing costs,
  broken backtester. Then re-run.
* Every experiment records the CEDEAR universe, data sources, data period, feature
  version, model, hyperparameters, train/validation/test periods, classification metrics,
  backtest metrics, benchmark metrics, transaction costs, slippage and a timestamp.

---

## 9. What a result means

A CEDEAR next-day direction model with, say, 53% balanced accuracy and a positive
backtest net of realistic costs is a *weak* signal at best. The honest framing is a
probability that is barely better than a coin, evaluated with enough data to know its
uncertainty. The dashboard presents probabilities, shows the benchmark next to the
strategy, and never renders a prediction as a certainty.
