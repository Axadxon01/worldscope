"""Bookmarks. Every query filters on the current user; row-level security backs that up."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from worldscope.db.models import Article, Bookmark, Source, User


class BookmarkError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class BookmarkView:
    id: uuid.UUID
    note: str | None
    created_at: datetime
    article_id: uuid.UUID
    title: str
    url: str
    source_name: str
    published_at: datetime | None


def _query(user: User):  # type: ignore[no-untyped-def]
    return (
        select(
            Bookmark.id,
            Bookmark.note,
            Bookmark.created_at,
            Article.public_id,
            Article.title,
            Article.canonical_url,
            Source.name,
            Article.published_at,
        )
        .join(Article, Article.id == Bookmark.article_id)
        .join(Source, Source.id == Article.source_id)
        .where(Bookmark.user_id == user.id)
    )


async def list_bookmarks(db: AsyncSession, user: User) -> list[BookmarkView]:
    rows = await db.execute(_query(user).order_by(Bookmark.created_at.desc()).limit(500))
    return [BookmarkView(*row) for row in rows.all()]


async def add_bookmark(
    db: AsyncSession, user: User, article_public_id: uuid.UUID, note: str | None
) -> BookmarkView:
    article_id = await db.scalar(select(Article.id).where(Article.public_id == article_public_id))
    if article_id is None:
        raise BookmarkError(404, "That article does not exist.")
    await db.execute(
        insert(Bookmark)
        .values(user_id=user.id, article_id=article_id, note=note)
        .on_conflict_do_update(constraint="uq_bookmarks_user_article", set_={"note": note})
    )
    await db.commit()
    row = (await db.execute(_query(user).where(Bookmark.article_id == article_id))).one()
    return BookmarkView(*row)


async def remove_bookmark(db: AsyncSession, user: User, bookmark_id: uuid.UUID) -> None:
    result = await db.execute(
        delete(Bookmark)
        .where(Bookmark.id == bookmark_id, Bookmark.user_id == user.id)
        .returning(Bookmark.id)
    )
    if result.first() is None:
        await db.rollback()
        raise BookmarkError(404, "That bookmark does not exist.")
    await db.commit()
