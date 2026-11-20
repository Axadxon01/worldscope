"""Sources and bookmarks for signed-in users."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status

from worldscope.api.deps import SessionDep
from worldscope.api.schemas import (
    BookmarkIn,
    BookmarkOut,
    BookmarksOut,
    CountryRef,
    SourceOut,
    SourcesOut,
)
from worldscope.api.security import CurrentAuth, require_internal_key
from worldscope.services import bookmarks, sources
from worldscope.services.bookmarks import BookmarkView

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_internal_key)], tags=["library"])


@router.get("/sources")
async def list_sources(_: CurrentAuth, db: SessionDep) -> SourcesOut:
    out = []
    for source, country_name in await sources.list_sources(db):
        country = (
            CountryRef(iso3=source.publisher_country_iso3, name=country_name)
            if source.publisher_country_iso3 and country_name
            else None
        )
        out.append(
            SourceOut(
                slug=source.slug,
                name=source.name,
                type=source.type,
                url=source.url,
                homepage=source.homepage,
                publisher_country=country,
                language=source.language,
                focus=list(source.focus),
                ownership=source.ownership,
                is_primary=source.is_primary,
                licence=source.licence,
                storage_policy=source.storage_policy,
                terms_url=source.terms_url,
                review_status=source.review_status,
                review_note=source.review_note,
                enabled=source.enabled,
                last_success_at=source.last_success_at,
                consecutive_failures=source.consecutive_failures,
            )
        )
    return SourcesOut(sources=out)


def _bookmark_out(view: BookmarkView) -> BookmarkOut:
    return BookmarkOut(**view.__dict__)


@router.get("/bookmarks")
async def list_bookmarks(context: CurrentAuth, db: SessionDep) -> BookmarksOut:
    views = await bookmarks.list_bookmarks(db, context.user)
    return BookmarksOut(bookmarks=[_bookmark_out(view) for view in views])


@router.post("/bookmarks", status_code=status.HTTP_201_CREATED)
async def add_bookmark(body: BookmarkIn, context: CurrentAuth, db: SessionDep) -> BookmarkOut:
    view = await bookmarks.add_bookmark(db, context.user, body.article_id, body.note)
    return _bookmark_out(view)


@router.delete("/bookmarks/{bookmark_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_bookmark(bookmark_id: uuid.UUID, context: CurrentAuth, db: SessionDep) -> Response:
    await bookmarks.remove_bookmark(db, context.user, bookmark_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
