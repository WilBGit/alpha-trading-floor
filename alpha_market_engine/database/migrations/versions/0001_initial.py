"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spy_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("spy_change_percent", sa.Numeric(18, 6), nullable=True),
        sa.Column("qqq_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("qqq_change_percent", sa.Numeric(18, 6), nullable=True),
        sa.Column("dia_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("dia_change_percent", sa.Numeric(18, 6), nullable=True),
        sa.Column("iwm_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("iwm_change_percent", sa.Numeric(18, 6), nullable=True),
        sa.Column("vix_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("vix_change_percent", sa.Numeric(18, 6), nullable=True),
        sa.Column("market_breadth", sa.Numeric(18, 6), nullable=True),
        sa.Column("advancers", sa.Integer(), nullable=True),
        sa.Column("decliners", sa.Integer(), nullable=True),
        sa.Column("up_volume", sa.Integer(), nullable=True),
        sa.Column("down_volume", sa.Integer(), nullable=True),
        sa.Column("raw_data_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_market_snapshots_snapshot_time", "market_snapshots", ["snapshot_time"])

    op.create_table(
        "ticker_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("snapshot_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=True),
        sa.Column("change_percent", sa.Numeric(18, 6), nullable=True),
        sa.Column("volume", sa.Integer(), nullable=True),
        sa.Column("relative_volume", sa.Numeric(18, 6), nullable=True),
        sa.Column("day_high", sa.Numeric(18, 6), nullable=True),
        sa.Column("day_low", sa.Numeric(18, 6), nullable=True),
        sa.Column("open_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("previous_close", sa.Numeric(18, 6), nullable=True),
        sa.Column("vwap", sa.Numeric(18, 6), nullable=True),
        sa.Column("rsi", sa.Numeric(18, 6), nullable=True),
        sa.Column("ema_9", sa.Numeric(18, 6), nullable=True),
        sa.Column("ema_20", sa.Numeric(18, 6), nullable=True),
        sa.Column("ema_50", sa.Numeric(18, 6), nullable=True),
        sa.Column("distance_from_vwap", sa.Numeric(18, 6), nullable=True),
        sa.Column("raw_data_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ticker_snapshots_symbol", "ticker_snapshots", ["symbol"])
    op.create_index("ix_ticker_snapshots_snapshot_time", "ticker_snapshots", ["snapshot_time"])
    op.create_index(
        "ix_ticker_snapshots_symbol_snapshot_time",
        "ticker_snapshots",
        ["symbol", "snapshot_time"],
    )

    op.create_table(
        "market_regimes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("regime", sa.Enum("RISK_ON", "RISK_OFF", "ROTATION", "TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOLATILITY", "LOW_VOLATILITY", "MIXED", "UNKNOWN", name="market_regime_name", native_enum=False, create_constraint=True, validate_strings=True), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("supporting_data_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_market_regimes_detected_at", "market_regimes", ["detected_at"])

    op.create_table(
        "market_catalysts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("headline", sa.String(length=512), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("symbols_json", sa.JSON(), nullable=True),
        sa.Column("category", sa.Enum("FED", "ECONOMIC_DATA", "EARNINGS", "GEOPOLITICAL", "ENERGY", "SECTOR", "COMPANY", "REGULATION", "OTHER", name="catalyst_category", native_enum=False, create_constraint=True, validate_strings=True), nullable=False),
        sa.Column("impact_score", sa.Integer(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column("content_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_market_catalysts_published_at", "market_catalysts", ["published_at"])

    op.create_table(
        "content_opportunities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opportunity_type", sa.String(length=64), nullable=False),
        sa.Column("symbols_json", sa.JSON(), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("observation", sa.Text(), nullable=False),
        sa.Column("supporting_data_json", sa.JSON(), nullable=True),
        sa.Column("importance_score", sa.Integer(), nullable=False),
        sa.Column("time_sensitivity_score", sa.Integer(), nullable=False),
        sa.Column("status", sa.Enum("DETECTED", "GENERATING", "GENERATED", "REJECTED", "POSTED", "EXPIRED", name="opportunity_status", native_enum=False, create_constraint=True, validate_strings=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_content_opportunities_detected_at", "content_opportunities", ["detected_at"])
    op.create_index("ix_content_opportunities_status", "content_opportunities", ["status"])

    op.create_table(
        "generated_posts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("post_type", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("normalized_hash", sa.String(length=128), nullable=False),
        sa.Column("symbols_json", sa.JSON(), nullable=True),
        sa.Column("market_regime", sa.Enum("RISK_ON", "RISK_OFF", "ROTATION", "TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOLATILITY", "LOW_VOLATILITY", "MIXED", "UNKNOWN", name="generated_post_market_regime", native_enum=False, create_constraint=True, validate_strings=True), nullable=True),
        sa.Column("source_opportunity_id", sa.Integer(), sa.ForeignKey("content_opportunities.id"), nullable=True),
        sa.Column("ai_provider", sa.String(length=64), nullable=False),
        sa.Column("ai_model", sa.String(length=128), nullable=False),
        sa.Column("fact_confidence_score", sa.Integer(), nullable=False),
        sa.Column("quality_score", sa.Integer(), nullable=False),
        sa.Column("duplicate_score", sa.Integer(), nullable=False),
        sa.Column("safety_status", sa.String(length=32), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.Enum("DRAFT", "VALIDATING", "APPROVED", "REJECTED", "POSTING", "POSTED", "FAILED", "SKIPPED", name="generated_post_status", native_enum=False, create_constraint=True, validate_strings=True), nullable=False),
        sa.Column("rejection_reason", sa.String(length=256), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_generated_posts_content_hash", "generated_posts", ["content_hash"])
    op.create_index("ix_generated_posts_normalized_hash", "generated_posts", ["normalized_hash"])
    op.create_index("ix_generated_posts_generated_at", "generated_posts", ["generated_at"])
    op.create_index("ix_generated_posts_published_at", "generated_posts", ["published_at"])
    op.create_index("ix_generated_posts_status", "generated_posts", ["status"])

    op.create_table(
        "published_posts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("generated_post_id", sa.Integer(), sa.ForeignKey("generated_posts.id"), nullable=False, unique=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("platform_post_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=True),
        sa.Column("likes", sa.Integer(), nullable=True),
        sa.Column("replies", sa.Integer(), nullable=True),
        sa.Column("reposts", sa.Integer(), nullable=True),
        sa.Column("bookmarks", sa.Integer(), nullable=True),
        sa.Column("engagement_rate", sa.Numeric(18, 6), nullable=True),
        sa.Column("last_metrics_update", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_published_posts_published_at", "published_posts", ["published_at"])

    op.create_table(
        "content_patterns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("pattern_name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("voice", sa.String(length=64), nullable=False),
        sa.Column("post_type", sa.String(length=64), nullable=False),
        sa.Column("template_structure", sa.Text(), nullable=False),
        sa.Column("times_used", sa.Integer(), nullable=False, server_default='0'),
        sa.Column("average_impressions", sa.Numeric(18, 6), nullable=True),
        sa.Column("average_engagement_rate", sa.Numeric(18, 6), nullable=True),
        sa.Column("average_replies", sa.Numeric(18, 6), nullable=True),
        sa.Column("performance_score", sa.Numeric(18, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "system_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.Enum("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", name="system_event_severity", native_enum=False, create_constraint=True, validate_strings=True), nullable=False),
        sa.Column("component", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_system_events_created_at", "system_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_system_events_created_at", table_name="system_events")
    op.drop_table("system_events")

    op.drop_table("content_patterns")

    op.drop_index("ix_published_posts_published_at", table_name="published_posts")
    op.drop_table("published_posts")

    op.drop_index("ix_generated_posts_published_at", table_name="generated_posts")
    op.drop_index("ix_generated_posts_generated_at", table_name="generated_posts")
    op.drop_index("ix_generated_posts_normalized_hash", table_name="generated_posts")
    op.drop_index("ix_generated_posts_content_hash", table_name="generated_posts")
    op.drop_table("generated_posts")

    op.drop_index("ix_content_opportunities_detected_at", table_name="content_opportunities")
    op.drop_table("content_opportunities")

    op.drop_index("ix_market_catalysts_published_at", table_name="market_catalysts")
    op.drop_table("market_catalysts")

    op.drop_index("ix_market_regimes_detected_at", table_name="market_regimes")
    op.drop_table("market_regimes")

    op.drop_index("ix_ticker_snapshots_symbol_snapshot_time", table_name="ticker_snapshots")
    op.drop_index("ix_ticker_snapshots_snapshot_time", table_name="ticker_snapshots")
    op.drop_index("ix_ticker_snapshots_symbol", table_name="ticker_snapshots")
    op.drop_table("ticker_snapshots")

    op.drop_index("ix_market_snapshots_snapshot_time", table_name="market_snapshots")
    op.drop_table("market_snapshots")
