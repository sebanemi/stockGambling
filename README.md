# StockGambling

Research platform for predicting the **next-day price direction of Argentine CEDEARs
traded on BYMA**.

> Educational and research software. Not investment advice. Model outputs are
> probabilities, never certainties.

---

## What the system predicts

The prediction target is the **CEDEAR quoted in ARS on BYMA** — *not* the underlying
foreign security.

```
AAPL CEDEAR (BYMA, ARS)  !=  AAPL (NASDAQ, USD)
```

They are related through the conversion ratio, the USD/ARS exchange rate and local
market behaviour, but they are **not interchangeable**:

```
CEDEAR price  ~=  underlying price x FX / ratio  +  local market effects
```

Everything in this repository is built around that distinction.

---

## Current status: Phase 6 - Baseline models done

| Phase | Scope                                                   | Status |
| ----- | ------------------------------------------------------- | ------ |
| **1** | **Repository, Docker Compose, PostgreSQL, Redis, API, web, health checks** | **Done** |
| **2** | **CEDEAR metadata, instruments, ratio history**         | **Done** |
| **3** | **Local CEDEAR / underlying / FX market data**         | **Done** |
| **4** | **Theoretical CEDEAR engine + premium/discount**       | **Done** |
| **5** | **Feature engineering (Wilder RSI/ATR, MACD signal, store, features API, feature.build task, leakage suite)** | **Done** |
| **6** | **Baselines: majority, random, logistic regression, XGBoost (`PredictionModel`, registry, dataset builder)** | **Done** |
| 7    | Walk-forward validation + leakage detection             | Pending |
| 8    | CEDEAR backtesting with transaction costs               | Pending |
| 9    | Prediction API                                          | Pending |
| 10   | Full dashboard                                          | Pending |

Theoretical prices, premium/discount, versioned feature snapshots and their
full provenance (ratio, FX, underlying price used) are computed and served
via API (`/theoretical-price`, `/features`, `/features/snapshots`).

---

## Quick start

```bash
git clone <repo-url> stockgambling
cd stockgambling
cp .env.example .env
docker compose up -d --build
```

| Service                | URL                            | Purpose                          |
| ---------------------- | ------------------------------ | -------------------------------- |
| Dashboard (Next.js)    | http://localhost:3000          | Web UI                           |
| API                    | http://localhost:8000          | FastAPI                          |
| OpenAPI / Swagger UI   | http://localhost:8000/docs     | Interactive API docs             |
| Readiness probe        | http://localhost:8000/health/ready | PostgreSQL + Redis status     |
| PostgreSQL             | `localhost:5432`               | `stockgambling` / `stockgambling` |
| Redis                  | `localhost:6379`               | Broker, results, cache           |

```bash
docker compose ps          # health status of all five services
docker compose logs -f api
docker compose down        # stop
docker compose down -v     # stop and DELETE the database volume
```

### Health endpoints

| Route                             | Meaning                                                   |
| --------------------------------- | --------------------------------------------------------- |
| `GET /health`                     | Alias, always `200` while the process lives                |
| `GET /health/live`                | Liveness. Never touches PostgreSQL or Redis                |
| `GET /api/v1/health/live`         | Same, under the versioned prefix                           |
| `GET /health/ready`               | Readiness. Verifies PostgreSQL + Redis, `503` if degraded  |
| `GET /api/v1`                     | Service discovery document                                 |
| `GET /api/v1/health/ready`        | Versioned readiness                                        |
| `GET http://localhost:3000/api/health` | Web container health, mirrors upstream readiness       |

---

## Repository layout

```
.
├── docker-compose.yml          # postgres, redis, api, web, worker
├── .env.example                # every supported setting, no secrets
├── Makefile                    # common developer commands
├── infra/postgres/init/        # one-shot DB bootstrap (extensions, UTC)
├── docs/                       # architecture, phases, data providers
├── backend/
│   ├── app/
│   │   ├── core/               # settings, logging, timezone/market-session helpers
│   │   ├── db/                 # SQLAlchemy engine, session factory, Base metadata
│   │   ├── api/v1/             # versioned routers
│   │   ├── infra/              # Redis cache client
│   │   ├── workers/            # Celery app and task registry
│   │   └── main.py             # FastAPI application factory
│   ├── migrations/             # Alembic (DSN injected from settings, never stored)
│   ├── tests/                  # pytest: unit / integration / leakage markers
│   └── pyproject.toml          # deps, ruff, mypy, pytest config
└── frontend/
    ├── app/                    # Next.js App Router
    │   ├── page.tsx            # infrastructure status dashboard
    │   └── api/health/         # web health bridge
    └── lib/api.ts              # typed backend client
```

---

## Local development (without Docker)

Requires Python 3.13 and Node.js 22+.

```bash
# Backend
cd backend
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp ../.env.example .env                              # point POSTGRES_HOST/REDIS_HOST at localhost
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

### Quality gates

```bash
# Backend
cd backend
ruff check .
ruff format --check .
mypy
pytest                       # add -m unit to skip integration tests
pytest -m "unit"             # no PostgreSQL/Redis required

# Frontend
cd frontend
npm run lint
npm run typecheck
npm run build
```

Or use the Makefile from the repository root: `make check`.

Tests that need PostgreSQL or Redis are marked `integration` and **skip** cleanly when
those services are unavailable.

---

## Architecture decisions

| Decision                         | Rationale                                                                                                  |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| PostgreSQL 17                    | Native `TIMESTAMPTZ`, `JSONB`, `pg_trgm` (CEDEAR symbol search) and range types for historical ratios.      |
| Redis                            | Celery broker + result backend + application cache. Persistence enabled so queued jobs survive a restart.     |
| Separate `api` and `worker`      | Training and backtests are CPU-heavy; they must never block a prediction request.                           |
| Celery, not RQ/arq              | Retries, result persistence, periodic tasks and routing per pipeline stage matter for a nightly pipeline.     |
| SQLAlchemy 2.0 + psycopg 3       | Typed 2.0-style ORM with the modern PostgreSQL driver.                                                      |
| Timezone-aware timestamps only   | `app/core/time.py` raises on naive datetimes. Cross-market date derivation is the main leakage risk.         |
| Market *dates* stored separately | A BYMA session and an underlying session are different days; merging them by index is how leaks appear.    |
| Server pinned to UTC             | Enforced by the init script and by a per-connection `SET timezone = 'UTC'`.                                 |
| Health = liveness vs readiness    | Liveness never touches dependencies, so a slow database cannot cause a restart loop.                        |
| Next.js standalone output         | The runtime image ships only the generated server bundle - no source, no full `node_modules`.                |
| Non-root containers              | Both images run as an unprivileged user.                                                                    |
| No Kubernetes                    | Explicitly out of scope. Five services is the whole topology.                                                |
| No hardcoded instruments         | The CEDEAR universe is ingested from a provider in Phase 2, never hardcoded in application code.             |
| Secrets out of the repo          | `.env` is git-ignored; a test scans the tree and fails the build if a credential is ever committed.          |

---

## Scientific-integrity rules

These are enforced by the architecture, not left to discipline:

1. **Chronological splits only.** No `shuffle=True`, no random K-fold on time series.
2. **Leakage tests are a separate pytest marker** (`-m leakage`). A model that leaks must fail CI.
3. **Historical conversion ratios.** Every theoretical price stores the `ratio_used`,
   `fx_used` and `underlying_price_used` that produced it.
4. **The test period is touched once.** No model selection, no threshold tuning on it.
5. **Suspiciously good results are a bug signal**, not a success signal.
6. **Backtests cost real money.** Transaction costs and slippage are configurable and
   non-zero by default; the buy-and-hold CEDEAR benchmark is always reported.
7. **Currencies are never mixed.** ARS and USD-adjusted returns are reported separately.

---

## Documentation

| Document                                    | Contents                                              |
| ------------------------------------------- | ----------------------------------------------------- |
| [docs/architecture.md](docs/architecture.md) | Services, data flow, module boundaries                |
| [docs/phases.md](docs/phases.md)             | Phase plan, deliverables, exit criteria               |
| [docs/data-providers.md](docs/data-providers.md) | Provider interfaces and the multi-market data model |
| [docs/methodology.md](docs/methodology.md)   | Target, features, walk-forward validation, backtesting |
| [backend/README.md](backend/README.md)       | Backend specifics                                     |
| [frontend/README.md](frontend/README.md)     | Frontend specifics                                    |

---

## Development status

Nothing in this repository produces a prediction yet. When a model is eventually added it
will be versioned, registered with its training/validation/test periods, evaluated
walk-forward, backtested against benchmarks, and served as a probability - never as a
certainty.
