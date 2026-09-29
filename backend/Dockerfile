# ===========================================================================
# StockGambling backend image
# ===========================================================================
# Two stages so that the runtime image contains no compiler toolchain and no
# package cache, keeping it small and reproducible.
# ===========================================================================

# ---------------------------------------------------------------------------
# Builder: create a self-contained virtualenv with all wheels resolved.
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# build-essential + git are only needed in case a wheel is missing for the
# pinned platform; they never reach the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependency layer is cached independently of the source code.
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip setuptools wheel \
    && pip install ".[dev]"

# ---------------------------------------------------------------------------
# Runtime: no compiler, no build cache, non-root user.
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_ENV=production \
    LOG_FORMAT=json

WORKDIR /app

RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home --home-dir /home/app app

COPY --from=builder /opt/venv /opt/venv

COPY --chown=app:app app ./app
COPY --chown=app:app tests ./tests
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini pyproject.toml README.md ./

# /app itself is root-owned so that no unprivileged process can drop files in
# the image; the working subdirectories above are owned by `app`.
RUN chown app:app /app

USER app
EXPOSE 8000
# Uses the stdlib so the image needs no extra packages.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live',timeout=4).status==200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
