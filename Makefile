# ===========================================================================
# StockGambling - developer shortcuts
# ===========================================================================
# The canonical commands are documented in README.md; this file only wraps the
# common ones. Every recipe is a plain shell command, so nothing is hidden.
# ===========================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose
BACKEND := backend
FRONTEND := frontend
VENV := $(BACKEND)/.venv

.PHONY: help up up-infra down down-v logs logs-api logs-worker ps build restart \
        check check-backend check-frontend test test-backend test-unit \
        test-integration test-leakage test-frontend test-in-docker \
        lint lint-frontend format typecheck typecheck-frontend \
        venv install-web dev-api dev-web db-shell redis-shell migrate revision

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- Docker ---------------------------------------------------------------

up: ## Start the whole stack (postgres, redis, api, web, worker)
	$(COMPOSE) up -d --build

build: ## Build images without starting containers
	$(COMPOSE) build

up-infra: ## Start only postgres and redis (for local, non-Docker development)
	$(COMPOSE) up -d postgres redis

down: ## Stop the stack, keeping volumes
	$(COMPOSE) down

down-v: ## Stop the stack and DELETE the database volume (destructive)
	$(COMPOSE) down -v

restart: ## Recreate the containers
	$(COMPOSE) up -d --build --force-recreate

ps: ## Show container status and health
	$(COMPOSE) ps

logs: ## Tail logs from every service
	$(COMPOSE) logs -f

logs-api: ## Tail API logs
	$(COMPOSE) logs -f api

logs-worker: ## Tail worker logs
	$(COMPOSE) logs -f worker

db-shell: ## Open a psql session
	$(COMPOSE) exec postgres psql -U stockgambling -d stockgambling

redis-shell: ## Open a redis-cli session
	$(COMPOSE) exec redis redis-cli

# --- Quality gates --------------------------------------------------------

check: check-backend check-frontend ## Run every lint, typecheck and test

check-backend: lint typecheck test-backend ## Backend quality gates

check-frontend: lint-frontend typecheck-frontend test-frontend ## Frontend quality gates

lint: ## ruff check + format check (backend)
	cd $(BACKEND) && .venv/bin/ruff check .
	cd $(BACKEND) && .venv/bin/ruff format --check .

lint-frontend: ## eslint (frontend)
	cd $(FRONTEND) && npm run lint

format: ## Apply ruff formatting and autofixes (backend)
	cd $(BACKEND) && .venv/bin/ruff check . --fix
	cd $(BACKEND) && .venv/bin/ruff format .

typecheck: ## mypy (backend)
	cd $(BACKEND) && .venv/bin/mypy

typecheck-frontend: ## tsc --noEmit (frontend)
	cd $(FRONTEND) && npm run typecheck

test: test-backend test-frontend ## Run all test suites

test-backend: ## pytest (all markers)
	cd $(BACKEND) && .venv/bin/pytest

test-unit: ## pytest, unit tests only (no PostgreSQL/Redis required)
	cd $(BACKEND) && .venv/bin/pytest -m unit

test-integration: ## pytest, integration tests only
	cd $(BACKEND) && .venv/bin/pytest -m integration

test-leakage: ## pytest, data-leakage tests only
	cd $(BACKEND) && .venv/bin/pytest -m leakage

test-frontend: ## Production build of the frontend
	cd $(FRONTEND) && npm run build

test-in-docker: ## Run the backend suite inside the built image
	$(COMPOSE) run --rm --no-deps -e APP_ENV=test -e POSTGRES_HOST=postgres \
		-e REDIS_HOST=redis api pytest -q

# --- Local development ----------------------------------------------------

venv: ## Create the backend virtualenv and install dev dependencies
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	cd $(BACKEND) && .venv/bin/pip install -e ".[dev]"

install-web: ## Install frontend dependencies
	cd $(FRONTEND) && npm install

dev-api: ## Run the API with autoreload (host)
	cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload

dev-web: ## Run the Next.js dev server (host)
	cd $(FRONTEND) && npm run dev

# --- Database -------------------------------------------------------------

migrate: ## Apply all Alembic migrations
	cd $(BACKEND) && .venv/bin/alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add instruments table"
	cd $(BACKEND) && .venv/bin/alembic revision --autogenerate -m "$(m)"
