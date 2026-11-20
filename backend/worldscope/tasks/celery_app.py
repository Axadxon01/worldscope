"""Celery application and beat schedule.

Run a worker:   celery -A worldscope.tasks.celery_app worker -l INFO
Run scheduler:  celery -A worldscope.tasks.celery_app beat -l INFO
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging

from worldscope.core.config import get_settings
from worldscope.core.logging import configure_logging

HEARTBEAT_INTERVAL_SECONDS = 60.0


def create_celery() -> Celery:
    settings = get_settings()
    app = Celery("worldscope", broker=settings.redis_url, include=["worldscope.tasks.system"])
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_ignore_result=True,
        timezone="UTC",
        enable_utc=True,
        # Ack after the task finishes so a crashed worker's task is re-delivered.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        beat_schedule={
            "worker-heartbeat": {
                "task": "worldscope.tasks.system.heartbeat",
                "schedule": HEARTBEAT_INTERVAL_SECONDS,
                "options": {"expires": HEARTBEAT_INTERVAL_SECONDS},
            },
            "purge-expired-sessions": {
                "task": "worldscope.tasks.system.purge_expired_sessions",
                "schedule": crontab(hour=3, minute=17),
            },
        },
    )
    return app


@setup_logging.connect  # type: ignore[untyped-decorator]
def _configure_celery_logging(**_: object) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)


celery_app = create_celery()
app = celery_app  # `celery -A worldscope.tasks.celery_app` looks for `app`
