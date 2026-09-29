# StockGambling - backend

FastAPI service + Celery worker for the StockGambling research platform.

## Layout

| Path                  | Purpose                                                  |
| --------------------- | -------------------------------------------------------- |
| `app/core/`           | Settings, logging, time/market-session helpers.           |
| `app/db/`             | SQLAlchemy engine, session factory, `Base` metadata.      |
| `app/api/v1/`         | Versioned HTTP routers.                                   |
| `app/workers/`        | Celery application and task registry.                     |
| `migrations/`         | Alembic migrations.                                       |
| `tests/`              | Pytest suite (`unit` / `integration` / `leakage`).        |

## Local usage

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## Quality gates

```powershell
ruff check .
ruff format --check .
mypy
pytest
```

## Design rules

* **No hardcoded instruments or conversion ratios.** CEDEAR metadata is ingested
  from a provider; see Phase 2.
* **No secrets in code.** All configuration comes from environment variables.
* **Timestamps are timezone-aware** and stored as `TIMESTAMPTZ`.
