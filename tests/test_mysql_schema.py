from __future__ import annotations

from sqlalchemy import inspect


EXPECTED_TABLES = {
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
    "post_metrics_snapshots",
    "published_posts",
    "content_patterns",
    "system_events",
}


def test_mysql_schema_tables_and_indexes(mysql_test_engine):
    insp = inspect(mysql_test_engine)
    assert EXPECTED_TABLES.issubset(set(insp.get_table_names()))

    ticker_indexes = {idx["name"]: idx for idx in insp.get_indexes("ticker_snapshots")}
    assert "ix_ticker_snapshots_symbol" in ticker_indexes
    assert "ix_ticker_snapshots_snapshot_time" in ticker_indexes
    assert "ix_ticker_snapshots_symbol_snapshot_time" in ticker_indexes

    price_action_indexes = {idx["name"]: idx for idx in insp.get_indexes("price_action_events")}
    assert "ix_price_action_events_symbol" in price_action_indexes
    assert "ix_price_action_events_event_type" in price_action_indexes
    assert "ix_price_action_events_detected_at" in price_action_indexes
    assert "ix_price_action_events_session_date" in price_action_indexes
    assert "ix_price_action_events_symbol_detected_at" in price_action_indexes
    price_action_unique = {tuple(constraint["column_names"]) for constraint in insp.get_unique_constraints("price_action_events")}
    assert ("event_id",) in price_action_unique

    observation_indexes = {idx["name"]: idx for idx in insp.get_indexes("market_observations")}
    assert "ix_market_observations_observation_type" in observation_indexes
    assert "ix_market_observations_detected_at" in observation_indexes
    assert "ix_market_observations_session_date" in observation_indexes
    assert "ix_market_observations_importance_score" in observation_indexes
    assert "ix_market_observations_observation_type_detected_at" in observation_indexes
    observation_unique = {tuple(constraint["column_names"]) for constraint in insp.get_unique_constraints("market_observations")}
    assert ("observation_key",) in observation_unique

    opportunity_indexes = {idx["name"]: idx for idx in insp.get_indexes("content_opportunities")}
    assert "ix_content_opportunities_status" in opportunity_indexes

    published_unique = {tuple(constraint["column_names"]) for constraint in insp.get_unique_constraints("published_posts")}
    assert ("platform_post_id",) in published_unique

    market_catalyst_unique = {tuple(constraint["column_names"]) for constraint in insp.get_unique_constraints("market_catalysts")}
    assert ("content_hash",) in market_catalyst_unique

    snapshot_columns = {col["name"]: col for col in insp.get_columns("market_snapshots")}
    assert snapshot_columns["snapshot_time"]["type"].__class__.__name__ == "DATETIME"
    assert snapshot_columns["spy_price"]["type"].__class__.__name__ == "DECIMAL"
    assert snapshot_columns["raw_data_json"]["type"].__class__.__name__ == "JSON"

    ticker_columns = {col["name"]: col for col in insp.get_columns("ticker_snapshots")}
    assert ticker_columns["price"]["type"].__class__.__name__ == "DECIMAL"
    assert ticker_columns["change_percent"]["type"].__class__.__name__ == "DECIMAL"
    assert ticker_columns["snapshot_time"]["type"].__class__.__name__ == "DATETIME"

    generated_columns = {col["name"]: col for col in insp.get_columns("generated_posts")}
    assert generated_columns["status"]["type"].__class__.__name__ == "VARCHAR"
    assert generated_columns["generated_at"]["type"].__class__.__name__ == "DATETIME"
    assert generated_columns["content_hash"]["type"].__class__.__name__ == "VARCHAR"

    published_columns = {col["name"]: col for col in insp.get_columns("published_posts")}
    assert published_columns["platform_post_id"]["type"].__class__.__name__ == "VARCHAR"
    assert published_columns["engagement_rate"]["type"].__class__.__name__ == "DECIMAL"

    event_columns = {col["name"]: col for col in insp.get_columns("system_events")}
    assert event_columns["severity"]["type"].__class__.__name__ == "VARCHAR"
