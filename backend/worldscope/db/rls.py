"""Row-level security: tell PostgreSQL which user the current transaction acts for.

User-owned tables carry a policy `user_id = app_current_user_id()`, which reads the
transaction-local setting `app.user_id`. `bind_user` sets it now and at the start of
every later transaction in the same session, so a commit cannot drop it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Connection, event, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

INFO_KEY = "app.user_id"


def _set(connection: Connection, user_id: str) -> None:
    connection.execute(select(func.set_config("app.user_id", user_id, True)))


def _on_begin(session: Session, _transaction: SessionTransaction, connection: Connection) -> None:
    user_id = session.info.get(INFO_KEY)
    if user_id:
        _set(connection, user_id)


async def bind_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    db.info[INFO_KEY] = str(user_id)
    if not event.contains(db.sync_session, "after_begin", _on_begin):
        event.listen(db.sync_session, "after_begin", _on_begin)
    await db.execute(select(func.set_config("app.user_id", str(user_id), True)))
