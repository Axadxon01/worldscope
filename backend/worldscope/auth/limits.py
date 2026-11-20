"""Rate limits and account lockout, counted in Redis.

- Per client IP: a fixed number of attempts per window for each sensitive action.
- Per account: after 5 failed sign-ins in 15 minutes, sign-in for that email pauses
  until the window ends. Unknown emails are counted the same way, so the response
  never reveals whether an account exists.

If Redis is unreachable these checks fail closed: sign-in stops rather than running
without limits.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from worldscope.auth.tokens import email_key

PREFIX = "worldscope:limit"


@dataclass(frozen=True)
class Rule:
    action: str
    limit: int
    window_seconds: int


LOGIN_PER_IP = Rule("login-ip", 20, 15 * 60)
SETUP_PER_IP = Rule("setup-ip", 10, 15 * 60)
INVITE_PER_IP = Rule("invite-ip", 20, 15 * 60)
PASSWORD_PER_USER = Rule("password-user", 10, 15 * 60)
ACCOUNT_FAILURES = Rule("login-account", 5, 15 * 60)


class LimitExceeded(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(retry_after)
        self.retry_after = max(1, retry_after)


class LimitsUnavailable(Exception):
    """Redis could not be reached."""


async def hit(redis: Redis, rule: Rule, subject: str) -> None:
    """Count one attempt; raise LimitExceeded once the window's limit is passed."""
    key = f"{PREFIX}:{rule.action}:{subject}"
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, rule.window_seconds, nx=True)
            pipe.ttl(key)
            count, _, ttl = await pipe.execute()
    except RedisError as exc:
        raise LimitsUnavailable from exc
    if int(count) > rule.limit:
        raise LimitExceeded(int(ttl))


async def check_account(redis: Redis, email: str) -> None:
    key = f"{PREFIX}:{ACCOUNT_FAILURES.action}:{email_key(email)}"
    try:
        count, ttl = await redis.get(key), await redis.ttl(key)
    except RedisError as exc:
        raise LimitsUnavailable from exc
    if count is not None and int(count) >= ACCOUNT_FAILURES.limit:
        raise LimitExceeded(int(ttl))


async def record_failure(redis: Redis, email: str) -> None:
    key = f"{PREFIX}:{ACCOUNT_FAILURES.action}:{email_key(email)}"
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ACCOUNT_FAILURES.window_seconds, nx=True)
            await pipe.execute()
    except RedisError as exc:
        raise LimitsUnavailable from exc


async def clear_failures(redis: Redis, email: str) -> None:
    try:
        await redis.delete(f"{PREFIX}:{ACCOUNT_FAILURES.action}:{email_key(email)}")
    except RedisError as exc:
        raise LimitsUnavailable from exc
