from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.database.models import PostMetricsSnapshot, ProductionCycle, PublicationJob, PublicationRecord, SystemEvent


def _clean(engine):
    assert engine.url.database == "alpha_market_test", f"destructive certification refused database={engine.url.database!r}"
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in ("post_metrics_snapshots", "system_events", "publication_jobs", "publication_records", "production_cycles"):
            connection.execute(text(f"TRUNCATE TABLE `{table}`"))
        connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))


def test_rollback_production_cycle_leaves_no_partial_rows(mysql_test_engine):
    _clean(mysql_test_engine); factory = sessionmaker(bind=mysql_test_engine, future=True); session = factory(); now = datetime.now(UTC)
    with pytest.raises(RuntimeError):
        with session.begin():
            session.add(ProductionCycle(cycle_id="r4-rb-cycle", runner_id="r4", analysis_id="r4", session_date="2026-07-14", operating_mode="DRY_RUN", market_state="OPEN", started_at=now, completed_at=None, analysis_confidence=80, opportunity_count=1, selected_candidate_count=1, draft_count=1, approved_draft_count=1, publication_decision="DRY_RUN", outcome="RUNNING"))
            session.add(SystemEvent(event_type="CYCLE_PARTIAL", severity="ERROR", component="r4", message="partial"))
            raise RuntimeError("injected cycle failure")
    session.close(); check = factory(); assert check.query(ProductionCycle).count() == 0; assert check.query(SystemEvent).count() == 0; check.add(ProductionCycle(cycle_id="r4-rb-cycle-valid", runner_id="r4", analysis_id="r4", session_date="2026-07-14", operating_mode="DRY_RUN", market_state="OPEN", started_at=now, completed_at=now, analysis_confidence=80, opportunity_count=0, selected_candidate_count=0, draft_count=0, approved_draft_count=0, publication_decision="DRY_RUN", outcome="SAFE_NO_PUBLICATION")); check.commit(); check.close(); mysql_test_engine.dispose()


def test_rollback_durable_job_does_not_mark_success(mysql_test_engine):
    _clean(mysql_test_engine); factory = sessionmaker(bind=mysql_test_engine, future=True); session = factory(); now = datetime.now(UTC)
    session.add(PublicationJob(job_type="RETRY_PUBLICATION", entity_id="r4-rb-job", idempotency_key="r4-rb-job-key", not_before=now, attempt_number=0, max_attempts=3, status="RUNNING")); session.commit()
    with pytest.raises(RuntimeError):
        with session.begin():
            session.add(SystemEvent(event_type="JOB_RESULT", severity="INFO", component="worker", message="result"))
            raise RuntimeError("injected job completion failure")
    session.close(); check = factory(); job = check.query(PublicationJob).filter_by(idempotency_key="r4-rb-job-key").one(); assert job.status == "RUNNING" and check.query(SystemEvent).count() == 0; check.close(); mysql_test_engine.dispose()


def test_rollback_publication_retry_is_not_successful(mysql_test_engine):
    _clean(mysql_test_engine); factory = sessionmaker(bind=mysql_test_engine, future=True); session = factory(); now = datetime.now(UTC)
    session.add(PublicationRecord(publication_request_id="r4-rb-pub", idempotency_key="r4-rb-pub-key", analysis_id="a", opportunity_id="o", opportunity_key="o", draft_id="d", draft_request_id="dr", platform="X", content_text="x", content_hash="r4-rb-pub-hash", content_pattern_key="p", publish_mode="LIMITED_AUTO", approval_status="APPROVE", decision="APPROVE", decision_reasons_json=[], state="SENDING", attempt_number=1, requested_at=now, source_detected_at=now)); session.commit()
    with pytest.raises(RuntimeError):
        with session.begin():
            row = session.query(PublicationRecord).filter_by(idempotency_key="r4-rb-pub-key").one(); row.state = "SUCCEEDED"; raise RuntimeError("injected publication persistence failure")
    session.close(); check = factory(); row = check.query(PublicationRecord).filter_by(idempotency_key="r4-rb-pub-key").one(); assert row.state == "SENDING" and check.query(PublicationRecord).count() == 1; check.close(); mysql_test_engine.dispose()


def test_rollback_metrics_ingestion_capture_key_reusable(mysql_test_engine):
    _clean(mysql_test_engine); factory = sessionmaker(bind=mysql_test_engine, future=True); session = factory(); now = datetime.now(UTC); snapshot = dict(capture_key="r4-rb-metrics-key", platform="X", platform_post_id="r4-rb-post", publication_request_id="r4-rb-pub", captured_at=now, analytics_window="15_MIN", impressions=1, likes=0, replies=0, reposts=0, bookmarks=0, engagement_rate=None, source_provider="fake", provider_payload_version="r4", availability_flags_json={})
    with pytest.raises(RuntimeError):
        with session.begin():
            session.add(PostMetricsSnapshot(**snapshot)); raise RuntimeError("injected metrics persistence failure")
    session.close(); check = factory(); assert check.query(PostMetricsSnapshot).count() == 0; check.close(); valid = factory(); valid.add(PostMetricsSnapshot(**snapshot)); valid.commit(); assert valid.query(PostMetricsSnapshot).count() == 1; valid.close(); mysql_test_engine.dispose()
