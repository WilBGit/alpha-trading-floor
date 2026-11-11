"""add phase 5D reconciliation jobs and metrics snapshots

Revision ID: 0006_publication_observability
Revises: 0005_publication_records
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_publication_observability"
down_revision = "0005_publication_records"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "publication_reconciliations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("publication_request_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("original_publication_state", sa.String(32), nullable=False),
        sa.Column("reconciliation_state", sa.String(48), nullable=False),
        sa.Column("platform_post_found", sa.Boolean(), nullable=False),
        sa.Column("platform_post_id", sa.String(128)),
        sa.Column("matched_content_hash", sa.String(128)),
        sa.Column("matched_content_text", sa.Text()),
        sa.Column("matched_created_at", sa.DateTime(timezone=True)),
        sa.Column("match_method", sa.String(64)),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("retry_allowed", sa.Boolean(), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_request_id", sa.String(128)),
        sa.Column("error_category", sa.String(64)),
        sa.Column("sanitized_error", sa.String(512)),
        sa.Column("warnings_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("publication_request_id", name="uq_publication_reconciliations_request_id"),
    )
    op.create_index("ix_publication_reconciliations_state", "publication_reconciliations", ["reconciliation_state"])

    op.create_table(
        "publication_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_type", sa.String(48), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_error", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_publication_jobs_idempotency_key"),
    )
    op.create_index("ix_publication_jobs_status", "publication_jobs", ["status"])
    op.create_index("ix_publication_jobs_not_before", "publication_jobs", ["not_before"])

    op.create_table(
        "post_metrics_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("capture_key", sa.String(192), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("platform_post_id", sa.String(128), nullable=False),
        sa.Column("publication_request_id", sa.String(64)),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analytics_window", sa.String(16), nullable=False),
        sa.Column("impressions", sa.Integer()),
        sa.Column("likes", sa.Integer()),
        sa.Column("replies", sa.Integer()),
        sa.Column("reposts", sa.Integer()),
        sa.Column("bookmarks", sa.Integer()),
        sa.Column("quote_posts", sa.Integer()),
        sa.Column("profile_clicks", sa.Integer()),
        sa.Column("url_clicks", sa.Integer()),
        sa.Column("engagement_rate", sa.Numeric(12, 6)),
        sa.Column("source_provider", sa.String(64), nullable=False),
        sa.Column("provider_payload_version", sa.String(32)),
        sa.Column("availability_flags_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("capture_key", name="uq_post_metrics_snapshots_capture_key"),
    )
    op.create_index("ix_post_metrics_snapshots_platform_post_id", "post_metrics_snapshots", ["platform_post_id"])
    op.create_index("ix_post_metrics_snapshots_captured_at", "post_metrics_snapshots", ["captured_at"])


def downgrade():
    op.drop_index("ix_post_metrics_snapshots_captured_at", table_name="post_metrics_snapshots")
    op.drop_index("ix_post_metrics_snapshots_platform_post_id", table_name="post_metrics_snapshots")
    op.drop_table("post_metrics_snapshots")
    op.drop_index("ix_publication_jobs_not_before", table_name="publication_jobs")
    op.drop_index("ix_publication_jobs_status", table_name="publication_jobs")
    op.drop_table("publication_jobs")
    op.drop_index("ix_publication_reconciliations_state", table_name="publication_reconciliations")
    op.drop_table("publication_reconciliations")
