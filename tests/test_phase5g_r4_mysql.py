from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.database.models import PostMetricsSnapshot, ProductionCycle, PublicationJob, PublicationRecord, SystemEvent
from alpha_market_engine.database.repository import Repository


def _clean(engine):
    assert engine.url.database == "alpha_market_test", f"destructive certification refused database={engine.url.database!r}"
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in ("post_metrics_snapshots", "system_events", "publication_jobs", "publication_records", "production_cycles", "publication_holds"):
            connection.execute(text(f"TRUNCATE TABLE `{table}`"))
        connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))


def _cycle(date, index, outcome, opportunities=0, selected=0, drafts=0, approved=0, confidence=80):
    now = datetime(2026, 7, 14, 12, index, tzinfo=UTC)
    return ProductionCycle(cycle_id=f"r4-summary-cycle-{index}", runner_id="r4", session_date=date, analysis_id=f"r4-analysis-{index}", operating_mode="DRY_RUN", market_state="OPEN", started_at=now, completed_at=now, analysis_confidence=confidence, opportunity_count=opportunities, selected_candidate_count=selected, draft_count=drafts, approved_draft_count=approved, publication_decision="DRY_RUN", outcome=outcome, error_category=None)


def test_mysql_session_summary_exact_value_matrix(mysql_test_engine):
    _clean(mysql_test_engine); session = sessionmaker(bind=mysql_test_engine, future=True)(); repo = Repository(session); date = "2026-07-14"; now = datetime(2026, 7, 14, 12, 30, tzinfo=UTC)
    session.add_all([_cycle(date, 1, "HEALTHY_NO_CANDIDATE", confidence=70), _cycle(date, 2, "HEALTHY_NO_OPPORTUNITY", confidence=75), _cycle(date, 3, "DEGRADED", 2, 1, 1, 1, 80), _cycle(date, 4, "DEGRADED", 1, 1, 1, 1, 85), _cycle(date, 5, "FAILED_PROVIDER", confidence=90), _cycle(date, 6, "SAFE_NO_PUBLICATION", 1, 1, 1, 0, 85)])
    pubs = [
        PublicationRecord(publication_request_id="r4-summary-post", idempotency_key="r4-summary-post-key", analysis_id="a", opportunity_id="o1", opportunity_key="o1", draft_id="d1", draft_request_id="dr1", platform="X", content_text="post", content_hash="h1", content_pattern_key="p", publish_mode="DRY_RUN", approval_status="APPROVE", decision="APPROVE", decision_reasons_json=[], state="SUCCEEDED", attempt_number=1, platform_post_id="post-1", requested_at=now, published_at=now, source_detected_at=now, created_at=now),
        PublicationRecord(publication_request_id="r4-summary-block-1", idempotency_key="r4-summary-block-1-key", analysis_id="a", opportunity_id="o2", opportunity_key="o2", draft_id="d2", draft_request_id="dr2", platform="X", content_text="blocked", content_hash="h2", content_pattern_key="p", publish_mode="DRY_RUN", approval_status="APPROVE", decision="BLOCKED", decision_reasons_json=["DAILY_HARD_MAX"], state="BLOCKED", attempt_number=0, requested_at=now, source_detected_at=now, created_at=now),
        PublicationRecord(publication_request_id="r4-summary-block-2", idempotency_key="r4-summary-block-2-key", analysis_id="a", opportunity_id="o3", opportunity_key="o3", draft_id="d3", draft_request_id="dr3", platform="X", content_text="blocked", content_hash="h3", content_pattern_key="p", publish_mode="DRY_RUN", approval_status="APPROVE", decision="BLOCKED", decision_reasons_json=["DUPLICATE"], state="BLOCKED", attempt_number=0, requested_at=now, source_detected_at=now, created_at=now),
        PublicationRecord(publication_request_id="r4-summary-unknown", idempotency_key="r4-summary-unknown-key", analysis_id="a", opportunity_id="o4", opportunity_key="o4", draft_id="d4", draft_request_id="dr4", platform="X", content_text="unknown", content_hash="h4", content_pattern_key="p", publish_mode="DRY_RUN", approval_status="APPROVE", decision="UNKNOWN", decision_reasons_json=[], state="UNKNOWN_PLATFORM_STATE", attempt_number=1, requested_at=now, source_detected_at=now, created_at=now),
    ]
    session.add_all(pubs)
    session.add(SystemEvent(event_type="PUBLICATION_BLOCKED", severity="INFO", component="X", message="duplicate", metadata_json={"reasons": ["DUPLICATE"]}, created_at=now))
    session.add_all([PublicationJob(job_type="RECONCILE_PUBLICATION", entity_id="r4-pending-1", idempotency_key="r4-pending-1", not_before=now, attempt_number=0, max_attempts=3, status="PENDING"), PublicationJob(job_type="INGEST_POST_METRICS", entity_id="r4-pending-2", idempotency_key="r4-pending-2", not_before=now, attempt_number=0, max_attempts=3, status="RUNNING"), PublicationJob(job_type="RETRY_PUBLICATION", entity_id="r4-failed", idempotency_key="r4-failed", not_before=now, attempt_number=3, max_attempts=3, status="FAILED_FINAL")])
    session.add_all([PostMetricsSnapshot(capture_key=f"r4-summary-m-{i}", platform="X", platform_post_id=f"post-{i}", publication_request_id="r4-summary-post", captured_at=now, analytics_window="15_MIN", impressions=i, likes=i, replies=0, reposts=0, bookmarks=0, engagement_rate=None, source_provider="fake", provider_payload_version="r4", availability_flags_json={}) for i in range(1, 4)])
    session.commit(); repo.upsert_global_hold(state="PLATFORM_STATE_HOLD", reason="one unresolved")
    actual = repo.get_session_summary(date)
    expected = {"cycles_completed": 6, "healthy_no_op_cycles": 2, "degraded_cycles": 2, "failed_cycles": 1, "opportunities_detected": 4, "post_candidates_selected": 3, "drafts_generated": 3, "drafts_approved": 2, "posts_published": 1, "posts_blocked": 2, "duplicate_suppressions": 1, "daily_hard_max": 10, "average_analysis_confidence": 80.83333333333333, "active_hold": "PLATFORM_STATE_HOLD", "unresolved_unknown_platform_states": 1, "pending_jobs": 2, "failed_jobs": 1, "metrics_snapshots": 3}
    for key, value in expected.items(): assert actual[key] == value, (key, actual[key], value)
    assert repo.get_session_summary("2026-07-15")["cycles_completed"] == 0
    session.close(); mysql_test_engine.dispose()
