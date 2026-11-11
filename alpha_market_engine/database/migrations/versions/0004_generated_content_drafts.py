"""add deterministic phase 5B generated draft records

Revision ID: 0004_generated_content_drafts
Revises: 0003_content_opportunities
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_generated_content_drafts"
down_revision = "0003_content_opportunities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generated_content_drafts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("draft_id", sa.String(length=64), nullable=False),
        sa.Column("draft_request_id", sa.String(length=64), nullable=False),
        sa.Column("analysis_id", sa.String(length=128), nullable=False),
        sa.Column("opportunity_id", sa.String(length=64), nullable=False),
        sa.Column("opportunity_key", sa.String(length=128), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("voice_profile", sa.String(length=64), nullable=False),
        sa.Column("draft_attempt", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("validation_failures_json", sa.JSON(), nullable=True),
        sa.Column("factual_claims_json", sa.JSON(), nullable=True),
        sa.Column("numeric_claims_json", sa.JSON(), nullable=True),
        sa.Column("symbol_claims_json", sa.JSON(), nullable=True),
        sa.Column("quality_score", sa.Integer(), nullable=False),
        sa.Column("final_decision", sa.String(length=32), nullable=False),
        sa.Column("final_decision_reasons_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("draft_id", name="uq_generated_content_drafts_draft_id"),
        sa.UniqueConstraint("content_hash", name="uq_generated_content_drafts_content_hash"),
    )
    for name, columns in (
        ("ix_generated_content_drafts_draft_request_id", ["draft_request_id"]),
        ("ix_generated_content_drafts_analysis_id", ["analysis_id"]),
        ("ix_generated_content_drafts_opportunity_id", ["opportunity_id"]),
        ("ix_generated_content_drafts_opportunity_key", ["opportunity_key"]),
        ("ix_generated_content_drafts_generated_at", ["generated_at"]),
        ("ix_generated_content_drafts_final_decision", ["final_decision"]),
    ):
        op.create_index(name, "generated_content_drafts", columns)


def downgrade() -> None:
    for name in (
        "ix_generated_content_drafts_final_decision", "ix_generated_content_drafts_generated_at",
        "ix_generated_content_drafts_opportunity_key", "ix_generated_content_drafts_opportunity_id",
        "ix_generated_content_drafts_analysis_id", "ix_generated_content_drafts_draft_request_id",
    ):
        op.drop_index(name, table_name="generated_content_drafts")
    op.drop_table("generated_content_drafts")
