"""add production control plane tables and analytics window

Revision ID: 0007_production_control_plane
Revises: 0006_publication_observability
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_production_control_plane"
down_revision = "0006_publication_observability"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("publication_jobs", sa.Column("analytics_window", sa.String(16), nullable=True))
    op.create_table(
        "production_cycles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("cycle_id", sa.String(64), nullable=False),
        sa.Column("runner_id", sa.String(128), nullable=False),
        sa.Column("analysis_id", sa.String(128)),
        sa.Column("session_date", sa.String(16), nullable=False),
        sa.Column("operating_mode", sa.String(32), nullable=False),
        sa.Column("market_state", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("analysis_confidence", sa.Integer()),
        sa.Column("opportunity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selected_candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("draft_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("approved_draft_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("publication_decision", sa.String(64)),
        sa.Column("publication_request_id", sa.String(64)),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("error_category", sa.String(64)),
        sa.Column("error_message_sanitized", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("cycle_id", name="uq_production_cycles_cycle_id"),
    )
    for name, column in (("ix_production_cycles_session_date", "session_date"), ("ix_production_cycles_started_at", "started_at"), ("ix_production_cycles_outcome", "outcome"), ("ix_production_cycles_publication_request_id", "publication_request_id")):
        op.create_index(name, "production_cycles", [column])
    op.create_table(
        "publication_holds",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("hold_name", sa.String(64), nullable=False),
        sa.Column("hold_state", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("source_reference", sa.String(128)),
        sa.Column("cleared_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("hold_name", name="uq_publication_holds_hold_name"),
    )
    op.bulk_insert(
        sa.table("publication_holds", sa.column("hold_name", sa.String), sa.column("hold_state", sa.String), sa.column("reason", sa.String)),
        [{"hold_name": "GLOBAL_PUBLICATION_HOLD", "hold_state": "CLEAR", "reason": "initial production control-plane state"}],
    )
    op.create_table(
        "system_leases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("lease_name", sa.String(128), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("host", sa.String(256), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("lease_name", name="uq_system_leases_lease_name"),
    )


def downgrade():
    op.drop_table("system_leases")
    op.drop_table("publication_holds")
    for name in ("ix_production_cycles_publication_request_id", "ix_production_cycles_outcome", "ix_production_cycles_started_at", "ix_production_cycles_session_date"):
        op.drop_index(name, table_name="production_cycles")
    op.drop_table("production_cycles")
    op.drop_column("publication_jobs", "analytics_window")
