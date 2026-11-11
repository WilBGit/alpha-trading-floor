"""add format_tag and YouTube audience metrics (CTR, retention, return viewers)
for Alpha Trading Floor measurement-before-automation loop.

Revision ID: 0008_youtube_audience_metrics
Revises: 0007_production_control_plane
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_youtube_audience_metrics"
down_revision = "0007_production_control_plane"
branch_labels = None
depends_on = None


def upgrade():
    # format_tag on core tables for A/B testing video formats
    op.add_column("generated_posts", sa.Column("format_tag", sa.String(64), nullable=True))
    op.add_column("publication_records", sa.Column("format_tag", sa.String(64), nullable=True))
    op.add_column("published_posts", sa.Column("format_tag", sa.String(64), nullable=True))

    # YouTube-specific audience metrics (beyond X engagement)
    op.add_column("post_metrics_snapshots", sa.Column("ctr", sa.Numeric(8, 4), nullable=True))
    op.add_column("post_metrics_snapshots", sa.Column("average_view_duration", sa.Numeric(10, 2), nullable=True))
    op.add_column("post_metrics_snapshots", sa.Column("retention_30s", sa.Numeric(5, 4), nullable=True))
    op.add_column("post_metrics_snapshots", sa.Column("return_viewer_rate", sa.Numeric(5, 4), nullable=True))
    op.add_column("post_metrics_snapshots", sa.Column("watch_time_minutes", sa.Numeric(12, 2), nullable=True))
    op.add_column("post_metrics_snapshots", sa.Column("youtube_video_id", sa.String(32), nullable=True))

    # Index for weekly digest queries by format
    op.create_index("ix_publication_records_format_tag", "publication_records", ["format_tag"])
    op.create_index("ix_post_metrics_snapshots_format_tag", "post_metrics_snapshots", ["format_tag"])  # if denormalized later


def downgrade():
    op.drop_index("ix_post_metrics_snapshots_format_tag", table_name="post_metrics_snapshots")
    op.drop_index("ix_publication_records_format_tag", table_name="publication_records")

    op.drop_column("post_metrics_snapshots", "watch_time_minutes")
    op.drop_column("post_metrics_snapshots", "return_viewer_rate")
    op.drop_column("post_metrics_snapshots", "retention_30s")
    op.drop_column("post_metrics_snapshots", "average_view_duration")
    op.drop_column("post_metrics_snapshots", "ctr")
    op.drop_column("post_metrics_snapshots", "youtube_video_id")

    op.drop_column("published_posts", "format_tag")
    op.drop_column("publication_records", "format_tag")
    op.drop_column("generated_posts", "format_tag")
