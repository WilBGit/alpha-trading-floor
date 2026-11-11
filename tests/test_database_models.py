from __future__ import annotations

from sqlalchemy import create_engine, inspect

from alpha_market_engine.database.base import Base
from alpha_market_engine.database import models  # noqa: F401 - register tables


def test_expected_tables_exist():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)

    assert set(inspector.get_table_names()) == {
        "market_snapshots",
        "ticker_snapshots",
        "market_regimes",
        "price_action_events",
        "market_observations",
        "market_catalysts",
        "content_opportunities",
        "generated_posts",
        "generated_content_drafts",
        "publication_records",
        "publication_reconciliations",
        "publication_jobs",
        "production_cycles",
        "publication_holds",
        "system_leases",
        "post_metrics_snapshots",
        "published_posts",
        "content_patterns",
        "system_events",
    }


def test_ticker_snapshot_indexes_exist():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)

    index_names = {index["name"] for index in inspector.get_indexes("ticker_snapshots")}
    assert "ix_ticker_snapshots_symbol" in index_names
    assert "ix_ticker_snapshots_snapshot_time" in index_names
    assert "ix_ticker_snapshots_symbol_snapshot_time" in index_names
