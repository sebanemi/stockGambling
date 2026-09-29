# Development phases

One phase at a time. Each phase ends with a verification step and a report, and the next
phase does not start until the current one is approved.

Legend: **Done** · *Pending*

---

## Phase 1 - Repository and infrastructure · **Done**

Deliverables

* `docker-compose.yml` with `postgres`, `redis`, `api`, `web`, `worker`
* PostgreSQL 17 with extensions (`pg_trgm`, `btree_gist`) and a UTC-pinned database
* Redis 7 with AOF, used as Celery broker + result backend + cache
* FastAPI app factory, CORS, request-id propagation, structured logging
* Liveness / readiness probes and a versioned API discovery document
* Celery app + task namespace, wired from settings
* Next.js dashboard with an infrastructure status page and a health bridge route
* Alembic configured with the DSN injected from settings (no credentials in files)
* pytest suite (`unit` / `integration` / `leakage` markers), ruff, mypy, eslint, tsc
* CI workflow and documentation

Exit criteria - all verified

1. `docker compose up -d --build` starts five healthy containers.
2. `/health/ready` returns `200` with `postgres: ok` and `redis: ok`.
3. A task dispatched to the broker completes and its result is readable from Redis.
4. `ruff check`, `ruff format --check`, `mypy`, `pytest` all pass locally and in the image.
5. `npm run lint`, `npm run typecheck`, `npm run build` all pass.
6. No credential is committed; a test enforces it.

---

## Phase 2 - CEDEAR metadata

Deliverables

* `instruments` and `instrument_ratio_history` tables
* Non-overlapping effective-date constraint on ratio history
* Provider interfaces: `CedearDataProvider`, `UnderlyingDataProvider`
* First concrete implementation ingesting BYMA / Caja de Valores metadata
* Ingestion job that **upserts** the universe (no hardcoded CEDEAR list)
* Tests: ratio lookup, historical ratio change, underlying mapping, idempotent re-ingest
* `GET /api/v1/cedears` and `GET /api/v1/cedears/{symbol}`

Exit criteria

* A CEDEAR is never assumed to have today's ratio for a past date.
* Re-running ingestion updates ratios without duplicating instruments.
* A newly listed CEDEAR appears without a code change.

---

## Phase 3 - Market data

Deliverables

* `local_price_history`, `underlying_price_history`, `fx_history` tables
* `FXDataProvider` abstraction; at least one configurable Argentine FX reference
* Normalised OHLCV ingestion with `TIMESTAMPTZ` plus explicit market dates
* Alignment module applying the as-of rule from `docs/architecture.md`
* Tests: FX alignment, timestamp alignment, cross-market date derivation, no blind merges

Exit criteria

* A BYMA session is never joined to a US session by index position.
* Traded value and trade count are stored when the provider exposes them.

---

## Phase 4 - Theoretical CEDEAR engine

Deliverables

* `theoretical = underlying_price x fx / ratio`, using the ratio valid **on that date**
* Premium / discount, persisted with `ratio_used`, `fx_used`, `underlying_price_used`
  and both timestamps
* Extensive tests: ratio changes mid-series, FX series change, missing underlying
  session, ratio changes to a different factor entirely

Exit criteria

* Recomputing a year of theoretical values after a ratio correction changes only the
  affected dates.
* Theoretical and actual are stored separately and never conflated.

---

## Phase 5 - Feature engineering

Deliverables

* CEDEAR technical features (returns 1/5/10/20d, SMA 5/10/20/50, EMA 10/20, RSI 14,
  MACD + signal, ATR, rolling volatility, volume change and ratio)
* Underlying equivalents
* FX features (returns, volatility, momentum, rolling change)
* Relative features: `expected_cedear_return`, `actual_cedear_return`,
  `local_underlying_divergence`
* Market features (S&P 500, Nasdaq, Dow, Russell 2000, VIX, sector ETF, Merval) behind
  a generic `market_data` reader so the model interface never changes
* Feature version registry

Exit criteria

* Every feature has a written definition, its inputs and its `as_of` timestamp.
* No feature is computed from data published after its `as_of`.

---

## Phase 6 - Baseline models

Deliverables

* `PredictionModel` interface: `fit`, `predict`, `predict_proba`, `save`, `load`,
  `get_metadata`
* Majority-class baseline, random baseline, logistic regression, XGBoost
* Model registry (`models`, `model_runs`) and experiment records
* Serialisation round-trip tests

Exit criteria

* The backtesting engine and the API are indifferent to the algorithm.
* A model can be loaded in a fresh process and reproduce identical predictions.

---

## Phase 7 - Walk-forward evaluation

Deliverables

* Chronological split utility - `shuffle=True` is banned and linted against
* Walk-forward folds driven by the actual data range, not hardcoded years
* Classification metrics: accuracy, balanced accuracy, precision, recall, F1, ROC-AUC,
  log loss, Brier score, and the majority baseline for reference
* Dedicated leakage test module (`-m leakage`): future underlying prices, future FX,
  future volume, future news, revised fundamentals, future conversion ratios, timezone
  errors, dataset-wide normalisation, shuffled splits

Exit criteria

* The test period is evaluated exactly once per model.
* A model that leaks fails the leakage suite.

---

## Phase 8 - CEDEAR backtesting

Deliverables

* Portfolio simulation on the **CEDEAR in ARS**, not the underlying
* Configurable transaction costs and slippage, non-zero by default
* Position sizing, available cash, entry/exit prices, portfolio value curve
* Benchmarks: buy-and-hold CEDEAR, cash / no-trade, optional underlying-equivalent
* Metrics: total and annualised return, volatility, Sharpe, max drawdown, win rate,
  trade count, average trade, profit factor, total costs
* ARS and USD-adjusted returns reported separately

Exit criteria

* Results are indistinguishable from the benchmark when the model always predicts one
  class (a deliberate null-model test).
* A strategy that cannot cover transaction costs shows a loss.

---

## Phase 9 - Prediction API

Deliverables

* `GET /api/v1/cedears/{symbol}/history|underlying|fx|theoretical-price|features|prediction`
* `GET /api/v1/models`, `GET /api/v1/models/{model_id}`
* `POST /api/v1/backtests`, `GET /api/v1/backtests/{backtest_id}`
* `GET /api/v1/experiments`
* Consistent response envelope, pagination, and error shapes

Exit criteria

* Every response value traces to stored data. Nothing is fabricated or defaulted.
* `probability_up + probability_down == 1`.

---

## Phase 10 - Frontend

Deliverables

* CEDEAR search
* Instrument panel: CEDEAR, underlying, both markets, current ratio
* Actual vs theoretical price chart with a premium/discount panel
* Multi-market chart (CEDEAR / underlying / USD/ARS) with honest timestamp alignment
* Prediction panel: up/down probability, model, version, horizon, recent performance
* Backtest results with benchmark comparison

Exit criteria

* Predictions are shown as probabilities, never as certainties.
* Charts never imply two different market sessions were simultaneous.

---

## Out of scope for the MVP

Neural networks, news sentiment, LLM predictions, live trading, automatic trading,
options, portfolio optimisation, intraday prediction. The architecture reserves room for
all of them; none of them may be implemented before the core pipeline is scientifically
valid.
