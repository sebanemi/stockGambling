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

## Phase 2 - CEDEAR metadata · **Done**

Deliverables

* `instruments` (`sg_instruments`) and `instrument_ratio_history` (`sg_instrument_ratio_history`) tables
* Non-overlapping effective-date constraint on ratio history (`[effective_from, effective_to)` per instrument)
* Provider interfaces: `CedearDataProvider`, `CedearRecord`, `CedearSnapshot`
* Concrete implementations: **COMAFI** home-banking BCR page and **Caja de Valores** CEDEAR list
* Ingestion job that **upserts** the universe (no hardcoded CEDEAR list), keyed on the CEDEAR symbol
* Ingestion audit trail (`sg_ingestion_runs`) with warnings, per-source and reconciled counts
* Reconciliation: ratio conflicts are quarantined and never guessed; disputed fields resolve to `null`
* Safe deactivation: an instrument is deactivated only when **every** expected source ran, with a `0.5` universe-retention guard
* `GET /api/v1/cedears` and `GET /api/v1/cedears/{symbol}` with `as_of`, pagination and ratio history
* `ingest.cedear_metadata` Celery task wrapping the same ingestion entry point used by tests
* Tests: ratio lookup, historical ratio change, underlying mapping, idempotent re-ingest

Exit criteria - all verified (284 tests, ruff, mypy)

* A CEDEAR is never assumed to have today's ratio for a past date (`as_of` honours the period table; a date before any period is `null`, never the nearest).
* Re-running ingestion updates ratios without duplicating instruments (a changed ratio closes the open period; an unchanged one is a no-op).
* A newly listed CEDEAR appears without a code change (the universe is whatever ingestion stored).
* A ratio conflict is recorded and excluded, never resolved by arbitrarily preferring a source.

---

## Phase 3 - Market data · **Done**

Deliverables

* `local_price_history`, `underlying_price_history`, `fx_history` tables
* `FXDataProvider` abstraction; Yahoo `USDARS=X` behind it (references, rate ARS per USD)
* `UnderlyingDataProvider` and `LocalPriceDataProvider` concrete implementations via Yahoo Finance chart (no API key; `AAPL.BA` for BYMA, plain/suffixed ticker for the underlying)
* Normalised OHLCV ingestion with `TIMESTAMPTZ` plus explicit market dates; no-trade slots rejected, never zeroed
* Alignment module (`app/alignment/asof.py`) applying the as-of rule and the one-session lag
* `ingest.cedear_prices` Celery task (per-symbol failures become warnings, one audit row per run)
* Tests: FX and underlying alignment, cross-market date derivation, no blind merges, idempotent re-ingest

Exit criteria - all verified (350 tests, ruff, mypy, `alembic check`)

* A BYMA session is never joined to a US session by index position.
* Traded value and trade count are stored when the provider exposes them (Yahoo does not; stored as `null` because the platform must not *invent* a turnover).
* Same-market-day underlying close is invisible to a prediction at the BYMA close (one-session lag, tested).

---

## Phase 4 - Theoretical CEDEAR engine · **Done**

Deliverables

* `theoretical = underlying_price x fx / ratio`, using the ratio valid **on that date**
* Premium / discount, persisted with `ratio_used`, `fx_used`, `underlying_price_used`
  and both timestamps
* Extensive tests: ratio changes mid-series, FX series change, missing underlying
  session, ratio changes to a different factor entirely

Exit criteria - all verified (theoretical tests, ruff, mypy)

* Recomputing a year of theoretical values after a ratio correction changes only the
  affected dates.
* Theoretical and actual are stored separately and never conflated.

---

## Phase 5 - Feature engineering · **Done**

Deliverables

* CEDEAR technical features (returns 1/5/10/20d, SMA 5/10/20/50, EMA 10/20,
  Wilder RSI 14, MACD + signal as EMA of the line, Wilder ATR 14, rolling
  volatility, volume change and ratio) computed strictly from bars before the
  prediction date (`app/features/cedear_technical.py`, shared maths in
  `app/features/indicators.py`)
* Underlying equivalents with the same indicator semantics
  (`app/features/underlying_technical.py`)
* FX features (returns, volatility, raw-difference momentum, rolling change)
  (`app/features/fx_features.py`)
* Relative features with the one-session underlying lag, no-lag FX and
  compounded expected return (`app/features/relative_features.py`,
  consistent with `app/alignment/asof.py`)
* Market features (Merval DB-backed; S&P 500 / Nasdaq / Dow / Russell 2000 /
  VIX / sector ETF return `None` until a provider and table exist - never
  fabricated) behind the generic `MarketDataReader`
  (`app/features/market_features.py`, `app/features/market_data.py`)
* Single build entry point (`app/features/build.py::compute_all_features`)
  used by both the API and the worker
* Feature version registry (`FeatureRegistry`, version `"1.0"`)
* Feature store (`sg_feature_snapshots`): ORM model, writer/reader with bulk
  insert, pagination and market-date range filters, history indexes
  (migrations `0004_feature_store.py`, `0005_feature_store_indexes.py`)
* Read API: `GET /api/v1/cedears/{symbol}/features` (live, read-only snapshot
  with `as_of` + `feature_version` provenance) and
  `GET /api/v1/cedears/{symbol}/features/snapshots` (paginated stored history)
* Worker task `feature.build` persisting one snapshot per instrument
  (per-symbol failures become warnings, all-`None` vectors are skipped)
* Tests: indicator maths (RSI bounds, Wilder vs naive MACD signal, ATR,
  volatility), store round-trip/bulk/pagination/range, API live + history,
  and a dedicated leakage suite (`-m leakage`): same-day underlying
  invisibility, prediction-date bar exclusion, future-FX invisibility,
  naive-datetime rejection

Exit criteria - all verified (ruff, mypy, `pytest -m "unit and not integration"`;
integration/leakage suites collect cleanly and run wherever PostgreSQL is up)

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
