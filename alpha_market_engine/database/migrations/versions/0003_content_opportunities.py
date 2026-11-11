"""add deterministic phase 5A content opportunity fields

Revision ID: 0003_content_opportunities
Revises: 0002_market_brain
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_content_opportunities"
down_revision = "0002_market_brain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = [
        ("opportunity_id", sa.String(length=64)),
        ("opportunity_key", sa.String(length=128)),
        ("analysis_id", sa.String(length=128)),
        ("primary_symbol", sa.String(length=16)),
        ("source_regime", sa.String(length=32)),
        ("source_regime_confidence", sa.Integer()),
        ("source_event_ids_json", sa.JSON()),
        ("source_observation_keys_json", sa.JSON()),
        ("source_divergence_types_json", sa.JSON()),
        ("facts_json", sa.JSON()),
        ("interpretation_boundary", sa.Text()),
        ("confidence_score", sa.Integer()),
        ("freshness_score", sa.Integer()),
        ("novelty_score", sa.Integer()),
        ("content_score", sa.Numeric(8, 2)),
        ("confirmation_state", sa.String(length=32)),
        ("decision", sa.String(length=32)),
        ("decision_reasons_json", sa.JSON()),
        ("cooldown_key", sa.String(length=128)),
        ("content_pattern_key", sa.String(length=128)),
        ("expires_at", sa.DateTime(timezone=True)),
        ("material_change", sa.Boolean(), sa.text("0")),
    ]
    for column in columns:
        if len(column) == 2:
            name, type_ = column
            op.add_column("content_opportunities", sa.Column(name, type_, nullable=True))
        else:
            name, type_, default = column
            op.add_column("content_opportunities", sa.Column(name, type_, nullable=False, server_default=default))
    op.create_index("ix_content_opportunities_analysis_id", "content_opportunities", ["analysis_id"])
    op.create_index("ix_content_opportunities_content_pattern_key", "content_opportunities", ["content_pattern_key"])
    op.create_index("ix_content_opportunities_decision", "content_opportunities", ["decision"])
    op.create_unique_constraint("uq_content_opportunities_opportunity_id", "content_opportunities", ["opportunity_id"])
    op.create_unique_constraint("uq_content_opportunities_opportunity_key", "content_opportunities", ["opportunity_key"])


def downgrade() -> None:
    op.drop_constraint("uq_content_opportunities_opportunity_key", "content_opportunities", type_="unique")
    op.drop_constraint("uq_content_opportunities_opportunity_id", "content_opportunities", type_="unique")
    op.drop_index("ix_content_opportunities_decision", table_name="content_opportunities")
    op.drop_index("ix_content_opportunities_content_pattern_key", table_name="content_opportunities")
    op.drop_index("ix_content_opportunities_analysis_id", table_name="content_opportunities")
    for name in (
        "material_change", "expires_at", "content_pattern_key", "cooldown_key", "decision_reasons_json",
        "decision", "confirmation_state", "content_score", "novelty_score", "freshness_score", "confidence_score",
        "interpretation_boundary", "facts_json", "source_divergence_types_json", "source_observation_keys_json",
        "source_event_ids_json", "source_regime_confidence", "source_regime", "primary_symbol", "analysis_id",
        "opportunity_key", "opportunity_id",
    ):
        op.drop_column("content_opportunities", name)
