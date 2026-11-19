# WorldScope developer commands. Run `make help` for the list.

COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
COMPOSE_PROD := docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file .env

.DEFAULT_GOAL := help
.PHONY: help setup dev down logs ps test test-backend test-frontend test-integration \
        lint format migrate migration prod-config

help: ## Show this list
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-18s %s\n", $$1, $$2}'

.env:
	@python3 scripts/setup_env.py

setup: .env ## Create .env with random secrets (never overwrites an existing one)
	@cd backend && uv sync
	@cd frontend && npm ci

dev: .env ## Build and start the whole stack, then wait until every service is healthy
	$(COMPOSE) up --build --detach --wait
	@echo "Web app:  http://localhost:$$(grep -E '^WEB_PORT=' .env | cut -d= -f2)"
	@echo "API docs: http://localhost:$$(grep -E '^API_PORT=' .env | cut -d= -f2)/docs"

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

logs: ## Follow logs from every service
	$(COMPOSE) logs --follow --tail=200

ps: ## Show service status and health
	$(COMPOSE) ps

test: test-backend test-frontend ## Lint, type-check and unit-test everything

test-backend: ## Backend: ruff, format check, mypy, unit tests
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest

test-frontend: ## Frontend: eslint and TypeScript
	cd frontend && npm run lint && npm run typecheck

test-integration: .env ## Backend tests against the running stack's PostgreSQL and Redis (separate test database)
	$(COMPOSE) up --detach --wait postgres redis
	$(COMPOSE) exec -T postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -tc "SELECT 1 FROM pg_database WHERE datname = '"'"'worldscope_test'"'"'" | grep -q 1 || createdb -U "$$POSTGRES_USER" worldscope_test'
	set -a && . ./.env && set +a && cd backend && \
	  WORLDSCOPE_INTEGRATION=1 ENVIRONMENT=test LLM_PROVIDER=fake \
	  DATABASE_URL="postgresql+psycopg://$$POSTGRES_USER:$$POSTGRES_PASSWORD@127.0.0.1:$$POSTGRES_PORT/worldscope_test" \
	  REDIS_URL="redis://:$$REDIS_PASSWORD@127.0.0.1:$$REDIS_PORT/1" \
	  uv run pytest

lint: ## Check formatting and lint without changing files
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint

format: ## Apply formatting and safe lint fixes
	cd backend && uv run ruff check --fix . && uv run ruff format .

migrate: .env ## Apply database migrations in the running stack
	$(COMPOSE) run --rm migrate

migration: ## Create a migration from model changes: make migration name="add articles"
	@test -n "$(name)" || (echo 'Usage: make migration name="what changed"' && exit 1)
	set -a && . ./.env && set +a && cd backend && uv run alembic revision --autogenerate -m "$(name)"

prod-config: .env ## Validate the production compose configuration
	$(COMPOSE_PROD) config --quiet && echo "Production compose configuration is valid."
