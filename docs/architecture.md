# Architecture

## 1. Topology

Five services, one network, no orchestrator, no microservices split.

```
                    ┌──────────────────────────────┐
   browser ────────▶│  web  (Next.js, :3000)       │
                    │  server-rendered dashboard  │
                    └──────────────┬───────────────┘
                                   │  HTTP (internal, :8000)
                                   ▼
                    ┌──────────────────────────────┐
                    │  api  (FastAPI, :8000)       │
                    │  /health  /api/v1/...        │
                    └──────┬────────────────┬──────┘
                           │                │
              ┌────────────▼───┐      ┌─────▼───────────┐
              │ postgres :5432 │      │ redis    :6379  │
              │ system of      │      │ broker / results│
              │ record         │      │ / cache         │
              └────────────────┘      └─────┬───────────┘
                                             │
                                    ┌────────▼─────────┐
                                    │ worker (Celery)  │
                                    │ ingest → train →│
                                    │ evaluate → back- │
                                    │ test             │
                                    └──────────────────┘
```

### Service responsibilities

| Service    | Does                                                                        | Must not                                                        |
| ---------- | --------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `postgres` | Stores instruments, ratio history, prices, features, models, backtests.      | —                                                               |
| `redis`    | Celery broker, Celery result backend, application cache.                     | Hold the only copy of anything (AOF is on, but it is not a system of record). |
| `api`      | Serves predictions and metadata. Stateless, horizontally scalable.           | Run training or backtests. That is the worker's job.              |
| `worker`   | Ingestion, feature builds, training, walk-forward evaluation, backtests.     | Serve HTTP requests.                                             |
| `web`      | Renders the dashboard. Server components read the API over the internal network. | Own business logic or compute features.                         |

### Why the API and the worker are separate

A walk-forward evaluation of a few hundred CEDEARs trains dozens of models. Running
that inside the API process would:

* hold the GIL / saturate a CPU and make `/health/ready` slow,
* risk an OOM that takes down prediction serving,
* make a long job indistinguishable from a stuck process.

Celery with a Redis broker gives retries, a persisted result store, and per-stage
queue routing for free.

---

## 2. Backend module boundaries

```
backend/app/
├── core/           No I/O beyond settings. Safe to import from anywhere.
│   ├── config.py     pydantic-settings, split by concern, cached singleton
│   ├── logging.py    structlog, JSON in production
│   └── time.py       ARGENTINA_TZ, session bounds, naive-datetime rejection
├── db/            SQLAlchemy engine, session factory, declarative Base
├── api/           HTTP layer only: routing, validation, serialisation
├── infra/         Outbound adapters: Redis cache, (Phase 3) HTTP providers
└── workers/       Celery app and task registry
```

Dependency direction is strictly inward: `api` and `workers` may import `db`,
`infra` and `core`. `core` imports nothing from the project. `db` imports only
`core`. Nothing imports from `api` or `workers`.

**Phase 2+ directories (planned, not yet created):**

```
app/models/      ORM tables (instruments, ratio history, prices, models, ...)
app/providers/   CedearDataProvider, UnderlyingDataProvider, FXDataProvider, ...
app/features/    Feature builders, one module per feature group
app/theoretical/ underlying x FX / ratio -> theoretical CEDEAR value
app/models_ml/   PredictionModel interface + Majority, LogisticRegression, XGBoost
app/validation/  Chronological splits, walk-forward folds
app/backtesting/ CEDEAR portfolio simulation, costs, benchmarks
app/registry/    Model registry and experiment tracking
```

---

## 3. Multi-market time handling

This is the single highest-risk area of the project, so it is designed in from day one.

**Rules**

1. Every instant is timezone-aware and stored as `TIMESTAMPTZ` in UTC. `app/core/time.py`
   raises on a naive datetime instead of guessing a zone.
2. A market *date* is derived from an instant using that market's own timezone
   (`to_market_date(instant, tz)`), never from the UTC date.
3. Local and underlying series are **never merged by index or by date** without an
   explicit as-of rule. The as-of rule is: *use the most recent observation whose
   timestamp is strictly before the prediction instant in the underlying market's
   timezone*.
4. The database session timezone is pinned to `UTC` by the init script and by a
   per-connection `SET timezone = 'UTC'`.

**Consequence for the MVP horizon.** A BYMA close at 18:00 ART occurs *before* the
next US close. So a `1d` CEDEAR prediction made at the BYMA close cannot use the same
day's US close - that would be look-ahead. The feature builder in Phase 5 will lag the
underlying series by one session for this reason, and the leakage tests in Phase 7 will
assert it.

---

## 4. Data model direction (Phase 2+)

Planned tables, listed so the schema is reviewable before it is written:

| Table                      | Purpose                                                    | Key constraint                                    |
| -------------------------- | ---------------------------------------------------------- | ------------------------------------------------- |
| `instruments`              | CEDEAR ⇄ underlying mapping, type, market, currency, ratio | one row per CEDEAR; `cedear_symbol` unique         |
| `instrument_ratio_history` | Ratio with effective dates and provenance                   | **no overlapping `[effective_from, effective_to)`** per instrument |
| `local_price_history`      | BYMA OHLCV, local `market_date` + UTC instants              | unique `(instrument_id, market_date)`              |
| `underlying_price_history` | Underlying OHLCV with **its own** market date/timezone      | unique `(instrument_id, market_date)`              |
| `fx_history`               | FX series per provider/instrument                           | unique `(pair, provider, market_date)`             |
| `market_data`              | Indices, sector ETFs, VIX, Merval                           | unique `(symbol, source, timestamp)`               |
| `features`                 | Materialised feature matrix per version                     | unique `(instrument_id, feature_version, as_of)`   |
| `models` / `model_runs`    | Registry entries and training runs                          | `model_id` + `model_version` unique                |
| `predictions`              | Served predictions with model + ratio + inputs provenance   | —                                                  |
| `backtests` / `backtest_trades` | Backtest runs and their trades                          | —                                                  |
| `experiments`              | Fully reproducible experiment records                       | —                                                  |
| `news`, `fundamentals`     | Future: point-in-time news and fundamentals                 | timestamped by *availability*, not by period       |

Every table carries a `source` column from day one so a new data vendor never requires a
schema rewrite. Ratio history is a separate table because **the current ratio is not
necessarily the historical ratio**.

---

## 5. Request and job flows

**Prediction request (Phase 9)**

```
GET /api/v1/cedears/AAPL/prediction
  → resolve instrument + ratio valid at as_of
  → load stored features for that as_of (never recompute from raw prices at request time)
  → load registered model by model_id + model_version
  → predict_proba
  → return { probability_up, probability_down, ratio, fx_reference,
            theoretical_price, actual_price, premium_discount, model_version }
```

**Training job (Phase 6-7)**

```
celery task: train.cedear_direction
  → build a chronological dataset from stored features
  → walk-forward folds (never a shuffled split)
  → fit on train, select on validation, evaluate once on test
  → register the model + run, record metrics, periods, feature version, hyperparameters
  → write artifacts outside the database, store only the path
```

**Backtest job (Phase 8)**

```
celery task: backtest.run
  → load the test-period predictions produced by the registered model
  → simulate the CEDEAR position in ARS with configurable costs and slippage
  → compare against buy-and-hold CEDEAR, cash, and the underlying-equivalent benchmark
  → report ARS and USD-adjusted returns separately
```

---

## 6. Health and observability

* Liveness (`/health/live`) touches **no** dependency, so a slow database can never cause
  a restart loop.
* Readiness (`/health/ready`) pings PostgreSQL and Redis and returns `503` when either is
  down. Compose gates `web` on this.
* The web container exposes `/api/health`, which mirrors upstream readiness, so a degraded
  API shows up as an unhealthy dashboard rather than a silently broken page.
* Logs are structured (structlog). JSON in the API image, console in development.
  DSNs are redacted before they are logged.
* Every HTTP response carries an `x-request-id` so a prediction request can be traced
  through the logs.
