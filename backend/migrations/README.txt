# Alembic migrations

The DSN is never stored in `alembic.ini`; it is resolved from application
settings at runtime (see `env.py`). To point a one-off migration at another
database, set `ALEMBIC_DATABASE_URL`.

Create a revision:

```powershell
alembic revision --autogenerate -m "add instruments table"
alembic upgrade head
```

Phase 1 ships an empty migration history on purpose: the schema is introduced
in Phase 2 (CEDEAR metadata) and generated from the ORM models, so the initial
revision is fully reviewable.
