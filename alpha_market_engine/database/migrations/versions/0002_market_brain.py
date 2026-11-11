"""deterministic market brain tables

Revision ID: 0002_market_brain
Revises: 0001_initial
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_market_brain"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_action_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=192), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("lifecycle", sa.String(length=32), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("supporting_values_json", sa.JSON(), nullable=True),
        sa.Column("session_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_price_action_events_event_id"),
    )
    op.create_index("ix_price_action_events_symbol", "price_action_events", ["symbol"])
    op.create_index("ix_price_action_events_event_type", "price_action_events", ["event_type"])
    op.create_index("ix_price_action_events_detected_at", "price_action_events", ["detected_at"])
    op.create_index("ix_price_action_events_session_date", "price_action_events", ["session_date"])
    op.create_index("ix_price_action_events_symbol_detected_at", "price_action_events", ["symbol", "detected_at"])

    op.create_table(
        "market_observations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("observation_key", sa.String(length=192), nullable=False),
        sa.Column("observation_type", sa.String(length=64), nullable=False),
        sa.Column("symbols_json", sa.JSON(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("importance_score", sa.Integer(), nullable=False),
        sa.Column("confidence_score", sa.Integer(), nullable=False),
        sa.Column("facts_json", sa.JSON(), nullable=True),
        sa.Column("interpretation_boundary", sa.Text(), nullable=False),
        sa.Column("source_events_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("observation_key", name="uq_market_observations_observation_key"),
    )
    op.create_index("ix_market_observations_observation_type", "market_observations", ["observation_type"])
    op.create_index("ix_market_observations_detected_at", "market_observations", ["detected_at"])
    op.create_index("ix_market_observations_session_date", "market_observations", ["session_date"])
    op.create_index("ix_market_observations_importance_score", "market_observations", ["importance_score"])
    op.create_index(
        "ix_market_observations_observation_type_detected_at",
        "market_observations",
        ["observation_type", "detected_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_market_observations_observation_type_detected_at", table_name="market_observations")
    op.drop_index("ix_market_observations_importance_score", table_name="market_observations")
    op.drop_index("ix_market_observations_session_date", table_name="market_observations")
    op.drop_index("ix_market_observations_detected_at", table_name="market_observations")
    op.drop_index("ix_market_observations_observation_type", table_name="market_observations")
    op.drop_table("market_observations")

    op.drop_index("ix_price_action_events_symbol_detected_at", table_name="price_action_events")
    op.drop_index("ix_price_action_events_session_date", table_name="price_action_events")
    op.drop_index("ix_price_action_events_detected_at", table_name="price_action_events")
    op.drop_index("ix_price_action_events_event_type", table_name="price_action_events")
    op.drop_index("ix_price_action_events_symbol", table_name="price_action_events")
    op.drop_table("price_action_events")
