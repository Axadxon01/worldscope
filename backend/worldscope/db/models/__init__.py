"""All models, imported here so Alembic and the app see one complete metadata."""

from worldscope.db.models.articles import Article
from worldscope.db.models.bookmarks import AUTH_SYSTEM_TABLES, USER_OWNED_TABLES, Bookmark
from worldscope.db.models.reference import Country, Region, Topic
from worldscope.db.models.sources import Source, SourceFetchRun
from worldscope.db.models.users import AuditEntry, Invitation, User, UserSession

__all__ = [
    "AUTH_SYSTEM_TABLES",
    "USER_OWNED_TABLES",
    "Article",
    "AuditEntry",
    "Bookmark",
    "Country",
    "Invitation",
    "Region",
    "Source",
    "SourceFetchRun",
    "Topic",
    "User",
    "UserSession",
]
