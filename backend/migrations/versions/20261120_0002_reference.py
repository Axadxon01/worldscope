"""Reference tables: regions, countries and topics.

Rows are loaded by `python -m worldscope.cli sync` from backend/config, not here, so
updating the reference data never needs a migration.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "regions",
        sa.Column("id", sa.SmallInteger(), sa.Identity(), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("parent_id", sa.SmallInteger(), nullable=True),
        sa.Column("is_focus", sa.Boolean(), server_default=sa.false(), nullable=False),
        *timestamps(),
        sa.CheckConstraint("kind IN ('continent', 'subregion')", name=op.f("ck_regions_kind")),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["regions.id"],
            name=op.f("fk_regions_parent_id_regions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_regions")),
        sa.UniqueConstraint("slug", name=op.f("uq_regions_slug")),
    )
    op.create_index(op.f("ix_regions_parent_id"), "regions", ["parent_id"])

    op.create_table(
        "countries",
        sa.Column("iso3", sa.String(3), nullable=False),
        sa.Column("iso2", sa.String(2), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("short_name", sa.String(100), nullable=False),
        sa.Column("region_id", sa.SmallInteger(), nullable=False),
        sa.Column("label_lat", sa.Double(), nullable=False),
        sa.Column("label_lon", sa.Double(), nullable=False),
        sa.Column("wikidata_qid", sa.String(16), nullable=True),
        *timestamps(),
        sa.CheckConstraint("iso3 ~ '^[A-Z]{3}$'", name=op.f("ck_countries_iso3_format")),
        sa.CheckConstraint("iso2 ~ '^[A-Z]{2}$'", name=op.f("ck_countries_iso2_format")),
        sa.CheckConstraint(
            "label_lat BETWEEN -90 AND 90", name=op.f("ck_countries_label_lat_range")
        ),
        sa.CheckConstraint(
            "label_lon BETWEEN -180 AND 180", name=op.f("ck_countries_label_lon_range")
        ),
        sa.ForeignKeyConstraint(
            ["region_id"],
            ["regions.id"],
            name=op.f("fk_countries_region_id_regions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("iso3", name=op.f("pk_countries")),
        sa.UniqueConstraint("iso2", name=op.f("uq_countries_iso2")),
    )
    op.create_index(op.f("ix_countries_region_id"), "countries", ["region_id"])

    op.create_table(
        "topics",
        sa.Column("id", sa.SmallInteger(), sa.Identity(), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("position", sa.SmallInteger(), nullable=True),
        *timestamps(),
        sa.CheckConstraint("kind IN ('category', 'emerging')", name=op.f("ck_topics_kind")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topics")),
        sa.UniqueConstraint("slug", name=op.f("uq_topics_slug")),
    )


def downgrade() -> None:
    op.drop_table("topics")
    op.drop_index(op.f("ix_countries_region_id"), table_name="countries")
    op.drop_table("countries")
    op.drop_index(op.f("ix_regions_parent_id"), table_name="regions")
    op.drop_table("regions")
