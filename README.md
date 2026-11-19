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

## Architecture decisions

- [0001: Technology stack](docs/adr/0001-technology-stack.md)
- [0002: Source and storage policy](docs/adr/0002-source-and-storage-policy.md)
