# ADR 0001: Technology stack

Status: accepted, 2026-11-20

## Decision

| Layer | Choice |
| --- | --- |
| Web app | Next.js 16 (App Router, Cache Components), TypeScript, Tailwind CSS 4, shadcn/ui |
| API | FastAPI with Pydantic v2, served by Uvicorn |
| Data access | SQLAlchemy 2 with the psycopg 3 driver, Alembic migrations |
| Database | PostgreSQL 16 with pgvector, pg_trgm and citext |
| Background work | Celery with Celery Beat, Redis as broker |
| AI | Provider-agnostic interface; Claude Haiku 5.5 for pipeline work, Sonnet 5.5 for analyst answers and reports |
| Deployment | Docker Compose; Caddy for HTTPS in production |

## Reasons

- One database. PostgreSQL holds relational data, full-text search (`tsvector`) and
  embeddings (pgvector), so hybrid search is one query and there is one thing to back
  up. Elasticsearch, Neo4j and PostGIS are left out until a measured need appears
  (search slower than one second, slow multi-hop graph queries, spatial queries).
- One database driver. psycopg 3 serves the async API and the synchronous workers and
  migrations, so there is a single `DATABASE_URL` format.
- Pydantic models are the contract everywhere: request and response bodies, settings,
  and the schemas the LLM must fill. Structured AI output is validated on our side and
  retried once with the validation errors; it is never trusted blindly.
- No vendor SDK outside `worldscope/ai/providers/`. Switching AI provider is a
  configuration change. Tests use a scripted fake provider and never call a paid API.
- The browser talks only to the Next.js app; Next.js calls the API server-side. API
  keys, internal hostnames and the database never face the internet.

## Consequences

- Connection pools are created in the API's lifespan so they live on the server's event
  loop; tests use `TestClient` as a context manager for the same reason.
- Next.js 16 enables Cache Components: pages render as static shells and anything
  live (the status panel) is fetched on the client or in route handlers.
- The Content-Security-Policy is set without nonces for now (`script-src 'self'
  'unsafe-inline'`). A nonce-based policy is part of Milestone 11 hardening.
