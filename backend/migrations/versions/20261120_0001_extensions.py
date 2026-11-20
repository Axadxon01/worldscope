"""Enable the PostgreSQL extensions the schema relies on.

- vector: embeddings for semantic search and event clustering (pgvector)
- pg_trgm: fuzzy matching of entity names and aliases
- citext: case-insensitive email addresses

Revision ID: 0001
Revises:
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Boolean, column, func, select, table

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("vector", "pg_trgm", "citext")


def upgrade() -> None:
    for name in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{name}"')


pg_extension = table("pg_extension", column("extname"), column("extowner"))


def downgrade() -> None:
    # Only drop what this role may drop. Where a database administrator installed an
    # extension (pgvector needs a superuser), the extension stays, as it would on any
    # managed PostgreSQL.
    bind = op.get_bind()
    for name in reversed(EXTENSIONS):
        may_drop = bind.execute(
            select(func.pg_has_role(pg_extension.c.extowner, "USAGE", type_=Boolean)).where(
                pg_extension.c.extname == name
            )
        ).scalar()
        if may_drop:
            op.execute(f'DROP EXTENSION IF EXISTS "{name}"')
