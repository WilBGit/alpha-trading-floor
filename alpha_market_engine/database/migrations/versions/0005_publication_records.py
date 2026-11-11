"""add phase 5C publication records

Revision ID: 0005_publication_records
Revises: 0004_generated_content_drafts
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_publication_records"
down_revision = "0004_generated_content_drafts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "publication_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("publication_request_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("analysis_id", sa.String(128), nullable=False),
        sa.Column("opportunity_id", sa.String(64), nullable=False),
        sa.Column("opportunity_key", sa.String(128), nullable=False),
        sa.Column("draft_id", sa.String(64), nullable=False),
        sa.Column("draft_request_id", sa.String(64), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("content_pattern_key", sa.String(128)),
        sa.Column("publish_mode", sa.String(32), nullable=False),
        sa.Column("approval_status", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(64), nullable=False),
        sa.Column("decision_reasons_json", sa.JSON()),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(64)),
        sa.Column("provider_request_id", sa.String(128)),
        sa.Column("platform_post_id", sa.String(128)),
        sa.Column("platform_response_status", sa.Integer()),
        sa.Column("retryable", sa.Boolean()),
        sa.Column("error_category", sa.String(64)),
        sa.Column("sanitized_error", sa.String(512)),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("source_detected_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_publication_records_idempotency_key"),
        sa.UniqueConstraint("platform_post_id", name="uq_publication_records_platform_post_id"),
    )
    for name, col in (
        ("ix_publication_records_draft_id", "draft_id"),
        ("ix_publication_records_opportunity_id", "opportunity_id"),
        ("ix_publication_records_created_at", "created_at"),
        ("ix_publication_records_state", "state"),
    ):
        op.create_index(name, "publication_records", [col])


def downgrade():
    for name in ("ix_publication_records_state", "ix_publication_records_created_at", "ix_publication_records_opportunity_id", "ix_publication_records_draft_id"):
        op.drop_index(name, table_name="publication_records")
    op.drop_table("publication_records")
