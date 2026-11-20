# syntax=docker/dockerfile:1.7
# One image runs the API, the Celery worker, the scheduler and migrations;
# docker compose picks the command.

FROM python:3.12-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# Builds behind a TLS-inspecting proxy can pass its CA certificate as the optional
# `extra_ca` build secret; normal builds ignore it.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir uv==0.11.32

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:${PATH}"
RUN groupadd --system app && useradd --system --gid app --uid 10001 --home-dir /app app
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app config ./config
COPY --chown=app:app worldscope ./worldscope
COPY --chown=app:app bin ./bin

USER app
EXPOSE 8000
# Listens on $PORT (hosting platforms set it) or 8000; see bin/start-api.
CMD ["/app/bin/start-api"]
