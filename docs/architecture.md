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
├── infra/         Outbound adapters: Redis cache
├── domain/        Project vocabulary: instrument type, program status, ratio formatting
├── models/        ORM tables: instruments, ratio history, ingestion runs, price history, FX
├── providers/     Provider contracts, registry and vendor adapters (comafi, cajadevalores, yahoo)
├── ingestion/     Ingestion services: metadata upsert and price/FX backfills
├── alignment/     As-of selectors (underlying lag, FX cutoff) - the leakage firewall
└── workers/       Celery app and task registry
```

Dependency direction is strictly inward: `api` and `workers` may import `db`,
`infra` and `core`. Each layer imports only itself, `core` and the layers below it.
`core` imports nothing from the project. Nothing imports from `api` or `workers`.

**Remaining directories (planned, not yet created):**

```
app/features/      Feature builders, one module per feature group
app/theoretical/   underlying x FX / ratio -> theoretical CEDEAR value
app/models_ml/     PredictionModel interface + Majority, LogisticRegression, XGBoost
app/validation/    Chronological splits, walk-forward folds
app/backtesting/   CEDEAR portfolio simulation, costs, benchmarks
app/registry/      Model registry and experiment tracking
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

## 4. Data model

Implemented tables (Phase 2-3), listed with the key constraint each one ships:

| Table                      | Purpose                                                    | Key constraint                                    |
| -------------------------- | ---------------------------------------------------------- | ------------------------------------------------- |
| `sg_instruments`           | CEDEAR ⇄ underlying mapping, type, market, currency, ratio, active flag, audit timestamps | one row per CEDEAR; symbol unique |
| `sg_instrument_ratio_history` | Ratio with effective dates and provenance                  | **no overlapping `[effective_from, effective_to)`** per instrument (exclusion constraint) |
| `sg_ingestion_runs`        | Per-run audit: job, status, counts, warnings, quarantine    | one row per run                                  |
| `sg_local_price_history`   | BYMA OHLCV, local `market_date` + UTC instants              | unique `(instrument_id, market_date)`              |
| `sg_underlying_price_history` | Underlying OHLCV with **its own** market date/timezone      | unique `(instrument_id, market_date)`              |
| `sg_fx_history`            | FX series per provider and pair                             | unique `(pair, source, market_date)`               |

Planned tables:

| Table                      | Purpose                                                    | Key constraint                                    |
| -------------------------- | ---------------------------------------------------------- | ------------------------------------------------- |
| `market_data`              | Indices, sector ETFs, VIX, Merval                           | unique `(symbol, source, timestamp)`               |
| `features`                 | Materialised feature matrix per version                     | unique `(instrument_id, feature_version, as_of)`   |
| `models` / `model_runs`    | Registry entries and training runs                          | `model_id` + `model_version` unique                |
| `predictions`              | Served predictions with model + ratio + inputs provenance   | —                                                  |
| `backtests` / `backtest_trades` | Backtest runs and their trades                          | —                                                  |
| `experiments`              | Fully reproducible experiment records                       | —                                                  |
| `news`, `fundamentals`     | Future: point-in-time news and fundamentals                 | timestamped by *availability*, not by period       |

Every table carries a `source` column from day one so a new data vendor never requires a
schema rewrite. Ratio history is a separate table because **the current ratio is not
necessarily the historical ratio**. Price tables are keyed on `market_date` (the *market's* own
trading day, in the venue's timezone) with a parallel aware-UTC `timestamp`; a re-ingest
updates a day in place (prices get restated), and a quiet day is absent, not zero.

---

## 5. Request and job flows

**Metadata ingestion (Phase 2)**

```
celery task: ingest.cedear_metadata
  → resolve the configured CEDEAR providers (union, unless CEDEAR_METADATA_PROVIDER is set)
  → fetch each source; a failure records a warning (and allows no deactivation)
  → translate each payload into CedearRecord tuples
  → reconcile fields across sources: conflicts are quarantined, never guessed
  → upsert instruments keyed on the symbol; re-ingest is idempotent
  → open a ratio period when a ratio differs; leave the open period untouched when it does not
  → deactivate instruments missing from *every* source, above a 0.5 retention floor
  → write one sg_ingestion_runs row recording counts, warnings and quarantines
```

**Market-data ingestion (Phase 3)**

```
celery task: ingest.cedear_prices
  → for each active instrument (or an explicit symbol list):
      local set     = LOCAL_PRICE_PROVIDER (Yahoo) -> AAPL.BA, BYMA calendar, ARS
      underlying set = UNDERLYING_PRICE_PROVIDER (Yahoo) -> AAPL, US calendar
  → fetch FX (FX_PROVIDER) for FX_DEFAULT_PAIR (USDARS=X)
  → per-fetch failures become warnings, never a failed run
  → upsert sg_local_price_history / sg_underlying_price_history keyed on (instrument_id, market_date)
      (a changed close updates the day in place; a missing day is absent, not zero)
  → upsert sg_fx_history keyed on (pair, source, market_date)
  → store tradeable references only: an unknown/missing/marketless underlying is a warning, not a guess
  → write one sg_ingestion_runs row; the job flushes but the caller owns the commit
```

Prediction-adjacent reads never go through the providers: `app/alignment/asof.py` reads
**stored** bars, with the underlying lagged one completed session and FX strictly before
the prediction instant.

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
