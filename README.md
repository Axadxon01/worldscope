# WorldScope Intelligence

A global news and geopolitical intelligence platform. It collects reporting from
legally reusable sources, groups articles into events, places them on a map, and lets
you question the collection with an AI analyst that cites every claim and says when
evidence is thin. Facts, reported claims, analysis and speculation are always labelled
apart.

## Status

Milestone 1 of 11 is complete: the project skeleton, running stack, design system and
CI. Pages exist for all eleven sections and say plainly what will fill them; nothing is
populated with sample data.

| Milestone | Scope | State |
| --- | --- | --- |
| 1 | Architecture, project setup, design system | Done |
| 2 | Database, accounts, source configuration | Next |
| 3 | Ingestion (GDELT, RSS, ReliefWeb) | |
| 4 | News feed and filtering | |
| 5 | Processing: entities, categories, deduplication, event clustering | |
| 6 | Country profiles and Overview | |
| 7 | Intelligence Map | |
| 8 | AI Analyst with citations (MVP release) | |
| 9 | Event relationships, timeline, Conflicts & Security | |
| 10 | Reports, watchlists, alerts | |
| 11 | Hardening and deployment | |

## Run it

Requirements: Docker with Compose v2.24 or newer, Python 3 and `make`.

```bash
python3 scripts/setup_env.py   # creates .env with random secrets
make dev                       # builds and starts everything, waits until healthy
```

Then open http://localhost:3000. The status panel at the bottom of the side rail shows
the API, database, queue, workers and AI provider, each checked live.

AI features stay off until you add `ANTHROPIC_API_KEY` to `.env` and run `make dev`
again.

| Service | Address | Notes |
| --- | --- | --- |
| Web app | http://localhost:3000 | Next.js |
| API | http://localhost:8000/docs | FastAPI; interactive docs outside production |
| PostgreSQL | 127.0.0.1:5432 | with pgvector, pg_trgm, citext |
| Redis | 127.0.0.1:6379 | task queue, cache, rate limits |

All ports bind to 127.0.0.1, so nothing is reachable from other machines.

## Develop

For editor tooling and tests outside Docker you also need
[uv](https://docs.astral.sh/uv/) and Node.js 22.

```bash
make setup             # .env, backend virtualenv, frontend packages
make test              # ruff, mypy and unit tests; eslint and TypeScript
make test-integration  # backend tests against the stack's PostgreSQL and Redis
make help              # every command
```

Integration tests use a separate `worldscope_test` database, so your local data is
never touched.

## Project layout

```text
backend/     FastAPI API, Celery workers and scheduler, Alembic migrations (Python 3.12)
frontend/    Next.js web app (TypeScript, Tailwind CSS, shadcn/ui)
config/      sources.yaml (every source and its terms), taxonomy.yaml (categories, regions)
infra/       Docker Compose for development and production, Caddy configuration
scripts/     setup_env.py (creates .env), check_feeds.py (verifies every feed live)
docs/        Architecture decisions, access requests
```

## Sources and data policy

Every source is listed in `config/sources.yaml` with its licence and review state. A
source is collected only after its terms have been reviewed. WorldScope stores
headlines, feed summaries and links, never full article text, never content behind a
paywall, and every item links to its original. See
[ADR 0002](docs/adr/0002-source-and-storage-policy.md).

Check that every feed still works:

```bash
cd backend && uv run python ../scripts/check_feeds.py
```

## Production

```bash
DOMAIN=intel.example.org make prod-config     # validate
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file .env up -d --build
```

Only Caddy is published (ports 80 and 443, automatic HTTPS). The API, database and
Redis have no public ports. Set `ENVIRONMENT=production` and `ANTHROPIC_API_KEY` in
`.env`; the backend refuses to start with weak secrets or a missing key.

## Deploy to Render

`render.yaml` describes a free ($0) deployment as a Render Blueprint. Everything runs
in Frankfurt, the closest Render region to Central Asia:

| Service | Render type | Plan | Monthly cost |
| --- | --- | --- | --- |
| `worldscope` (web app) | Web service | Free | $0 |
| `worldscope-api` | Web service | Free | $0 |
| `worldscope-db` | PostgreSQL 16 | Free (1 GB, expires 30 days after creation) | $0 |
| `worldscope-kv` (task queue) | Key Value | Free (25 MB, in memory) | $0 |

What the free plan costs in behaviour:

- Both web services sleep after 15 minutes without visits and take about a minute to
  wake. The Settings page says so while the API is waking.
- There are no background workers (Render has no free plan for them), so nothing is
  collected on a schedule. The API runs with `WORKERS_ENABLED=false`, and the status
  page shows workers as "Not deployed" rather than as a fault.
- The free database is deleted 30 days after it is created, unless you upgrade it
  before then. Render emails a warning first.
- There is no pre-deploy command, so the API applies migrations as it starts
  (`RUN_MIGRATIONS_ON_START=true`, see `backend/bin/start-api`).
- Free services cannot receive private-network traffic, so the web app reaches the API
  through its public URL: set `API_INTERNAL_URL` on `worldscope` to the API's
  `https://...onrender.com` address once the API exists.

The database and Key Value accept private-network connections only. AI features start
turned off (`LLM_PROVIDER=none`); to turn them on, set `LLM_PROVIDER=anthropic` and
`ANTHROPIC_API_KEY` on `worldscope-api`.

To deploy: in the Render Dashboard choose New > Blueprint, select this repository and
approve the plan, then set `API_INTERNAL_URL` as above. Later pushes to `main` deploy
automatically.

### Moving to an always-on deployment

Collecting news on a schedule needs a worker, which needs paid plans. Prices from
[render.com/pricing](https://render.com/pricing), checked 20 November 2026:

| Change | Plan | Monthly cost |
| --- | --- | --- |
| `worldscope-api` stops sleeping and becomes reachable privately | Starter, 0.5 CPU, 512 MB | $7 |
| Add `worldscope-worker` (Celery worker with `--beat`, one instance only) | Starter, 0.5 CPU, 512 MB | $7 |
| `worldscope-db` stops expiring | Basic, 256 MB | $6 plus storage |

With those in place: give the worker the same environment as the API, set
`WORKERS_ENABLED=true` and replace `RUN_MIGRATIONS_ON_START` with the pre-deploy
command `alembic upgrade head`, and point the web app at the API's private
`host:port` (`API_INTERNAL_HOSTPORT`). The git history of `render.yaml` holds this
layout in full (commit `1dda8be`).

## Architecture decisions

- [0001: Technology stack](docs/adr/0001-technology-stack.md)
- [0002: Source and storage policy](docs/adr/0002-source-and-storage-policy.md)
