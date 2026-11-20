"""Sources, their fetch history, and articles.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEARCH_VECTOR = (
    "to_tsvector("
    "CASE language WHEN 'en' THEN 'english'::regconfig "
    "WHEN 'ru' THEN 'russian'::regconfig ELSE 'simple'::regconfig END, "
    "coalesce(title, '') || ' ' || coalesce(snippet, ''))"
)


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
        "sources",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("homepage", sa.Text(), nullable=False),
        sa.Column("publisher_country_iso3", sa.String(3), nullable=True),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column(
            "focus",
            postgresql.ARRAY(sa.String(64)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column("ownership", sa.String(32), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("licence", sa.Text(), nullable=True),
        sa.Column("storage_policy", sa.String(16), nullable=False),
        sa.Column("terms_url", sa.Text(), nullable=True),
        sa.Column("review_status", sa.String(16), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "fetch_interval_seconds", sa.Integer(), server_default=sa.text("900"), nullable=False
        ),
        sa.Column("rate_limit", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        *timestamps(),
        sa.CheckConstraint("type IN ('rss', 'official', 'api')", name=op.f("ck_sources_type")),
        sa.CheckConstraint(
            "ownership IS NULL OR ownership IN ('public_broadcaster', 'state', 'state_funded',"
            " 'government', 'intergovernmental', 'private', 'nonprofit')",
            name=op.f("ck_sources_ownership"),
        ),
        sa.CheckConstraint(
            "storage_policy IN ('link_only', 'snippet')", name=op.f("ck_sources_storage_policy")
        ),
        sa.CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_sources_review_status"),
        ),
        sa.CheckConstraint(
            "NOT enabled OR (review_status = 'approved' AND retired_at IS NULL)",
            name=op.f("ck_sources_enabled_requires_approval"),
        ),
        sa.CheckConstraint("fetch_interval_seconds >= 60", name=op.f("ck_sources_fetch_interval")),
        sa.CheckConstraint(
            "review_status <> 'approved' OR (licence IS NOT NULL AND terms_url IS NOT NULL)",
            name=op.f("ck_sources_approved_has_terms"),
        ),
        sa.ForeignKeyConstraint(
            ["publisher_country_iso3"],
            ["countries.iso3"],
            name=op.f("fk_sources_publisher_country_iso3_countries"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("slug", name=op.f("uq_sources_slug")),
        sa.UniqueConstraint("url", name=op.f("uq_sources_url")),
    )
    op.create_index(
        op.f("ix_sources_publisher_country_iso3"), "sources", ["publisher_country_iso3"]
    )

    op.create_table(
        "source_fetch_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("items_seen", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("items_new", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')", name=op.f("ck_source_fetch_runs_status")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_source_fetch_runs_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_fetch_runs")),
    )
    op.create_index(
        "ix_source_fetch_runs_source_started",
        "source_fetch_runs",
        ["source_id", sa.text("started_at DESC")],
    )

    op.create_table(
        "articles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "public_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.LargeBinary(), nullable=False),
        sa.Column("content_hash", sa.LargeBinary(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("near_dup_of", sa.BigInteger(), nullable=True),
        sa.Column("processing_status", sa.String(16), server_default="new", nullable=False),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "tsv", postgresql.TSVECTOR(), sa.Computed(SEARCH_VECTOR, persisted=True), nullable=False
        ),
        *timestamps(),
        sa.CheckConstraint("length(title) > 0", name=op.f("ck_articles_title_not_empty")),
        sa.CheckConstraint("octet_length(url_hash) = 32", name=op.f("ck_articles_url_hash_sha256")),
        sa.CheckConstraint(
            "processing_status IN ('new', 'processing', 'processed', 'failed')",
            name=op.f("ck_articles_processing_status"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_articles_source_id_sources"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["near_dup_of"],
            ["articles.id"],
            name=op.f("fk_articles_near_dup_of_articles"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_articles")),
        sa.UniqueConstraint("public_id", name=op.f("uq_articles_public_id")),
        sa.UniqueConstraint("url_hash", name=op.f("uq_articles_url_hash")),
    )
    op.create_index(op.f("ix_articles_source_id"), "articles", ["source_id"])
    op.create_index(op.f("ix_articles_content_hash"), "articles", ["content_hash"])
    op.create_index("ix_articles_published_at", "articles", [sa.text("published_at DESC")])
    op.create_index("ix_articles_tsv", "articles", ["tsv"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("articles")
    op.drop_table("source_fetch_runs")
    op.drop_index(op.f("ix_sources_publisher_country_iso3"), table_name="sources")
    op.drop_table("sources")
