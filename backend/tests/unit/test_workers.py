from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from worldscope.services.system_status import (
    ComponentStatus,
    WorkerStatus,
    check_workers,
    overall,
)
from worldscope.tasks import system
from worldscope.tasks.celery_app import HEARTBEAT_INTERVAL_SECONDS, celery_app
from worldscope.tasks.system import write_heartbeat

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


class SyncRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int | None]] = {}

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = (value, ex)


class AsyncRedis:
    def __init__(self, value: str | None) -> None:
        self.value = value

    async def get(self, _: str) -> str | None:
        return self.value


def test_heartbeat_writes_utc_timestamp_with_ttl() -> None:
    redis = SyncRedis()
    stamp = write_heartbeat(redis, "hb", 300, now=NOW)  # type: ignore[arg-type]
    assert redis.store["hb"] == ("2026-11-20T12:00:00+00:00", 300)
    assert stamp.endswith("+00:00")


async def test_recent_heartbeat_is_ok() -> None:
    beat = (NOW - timedelta(seconds=45)).isoformat()
    status = await check_workers(AsyncRedis(beat), "hb", now=NOW)  # type: ignore[arg-type]
    assert status.status == "ok"
    assert status.age_seconds == 45


async def test_old_heartbeat_is_stale() -> None:
    beat = (NOW - timedelta(minutes=10)).isoformat()
    status = await check_workers(AsyncRedis(beat), "hb", now=NOW)  # type: ignore[arg-type]
    assert status.status == "stale"


async def test_missing_or_garbled_heartbeat_is_unknown() -> None:
    for value in (None, "not-a-date"):
        status = await check_workers(AsyncRedis(value), "hb", now=NOW)  # type: ignore[arg-type]
        assert status.status == "unknown"


async def test_missing_heartbeat_is_disabled_when_no_workers_are_expected() -> None:
    status = await check_workers(AsyncRedis(None), "hb", now=NOW, expected=False)  # type: ignore[arg-type]
    assert status.status == "disabled"


async def test_a_real_heartbeat_wins_even_when_workers_are_not_expected() -> None:
    beat = (NOW - timedelta(seconds=10)).isoformat()
    status = await check_workers(AsyncRedis(beat), "hb", now=NOW, expected=False)  # type: ignore[arg-type]
    assert status.status == "ok"


def test_overall_state() -> None:
    ok, down = ComponentStatus(status="ok"), ComponentStatus(status="down")
    alive, missing = WorkerStatus(status="ok"), WorkerStatus(status="unknown")
    assert overall(ok, ok, alive) == "ok"
    assert overall(ok, ok, missing) == "degraded"
    assert overall(ok, ok, WorkerStatus(status="disabled")) == "ok"
    assert overall(down, ok, alive) == "down"
    assert overall(ok, down, alive) == "down"


def test_beat_schedules_the_heartbeat_task() -> None:
    entry = celery_app.conf.beat_schedule["worker-heartbeat"]
    assert entry["task"] == "worldscope.tasks.system.heartbeat"
    assert entry["schedule"] == HEARTBEAT_INTERVAL_SECONDS
    assert celery_app.conf.task_acks_late is True


def test_worker_start_queues_a_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    queued: list[bool] = []
    monkeypatch.setattr(system.heartbeat, "delay", lambda: queued.append(True))
    system.heartbeat_on_start(sender=None)
    assert queued == [True]
