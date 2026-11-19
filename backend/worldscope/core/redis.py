"""Async Redis client factory.

The API creates one client per process in its lifespan (see api/main.py) so the
client and its connection pool live on the server's event loop.
"""

from __future__ import annotations

from redis.asyncio import Redis


def create_redis(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)
