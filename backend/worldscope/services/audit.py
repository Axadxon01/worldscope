"""Append entries to the audit log. The caller commits with its own change."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from worldscope.db.models import AuditEntry


def record(
    db: AsyncSession,
    action: str,
    *,
    user_id: uuid.UUID | None = None,
    target: str | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEntry(
            action=action,
            user_id=user_id,
            target=target[:200] if target else None,
            ip=ip,
            details=details or {},
        )
    )
