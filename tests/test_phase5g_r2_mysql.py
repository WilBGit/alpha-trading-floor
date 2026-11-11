from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.database.models import PostMetricsSnapshot, ProductionCycle, PublicationJob, PublicationRecord
from alpha_market_engine.database.repository import Repository


@pytest.fixture(autouse=True)
def clean_r2_mysql_matrix(mysql_test_engine):
    with mysql_test_engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in ("post_metrics_snapshots", "publication_jobs", "publication_records", "production_cycles", "publication_holds", "system_leases"):
            connection.execute(text(f"TRUNCATE TABLE `{table}`"))
        connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    yield


def publication(*, state="SUCCEEDED", request_id="r2-pub", post_id="r2-post"):
    now = datetime.now(UTC)
    return PublicationRecord(
        publication_request_id=request_id, idempotency_key=f"idem-{request_id}", analysis_id="r2-analysis",
        opportunity_id="r2-opp", opportunity_key="QQQ_LEADERSHIP", draft_id="r2-draft", draft_request_id="r2-draft-req",
        platform="X", content_text="$QQQ is leading $SPY.", content_hash=f"hash-{request_id}", content_pattern_key="QQQ_LEADERSHIP",
        publish_mode="DRY_RUN", approval_status="APPROVE", decision="APPROVE", decision_reasons_json=[], state=state,
        attempt_number=1, platform_post_id=post_id if state == "SUCCEEDED" else None, requested_at=now, published_at=now if state == "SUCCEEDED" else None,
        source_detected_at=now,
    )


def test_mysql_lease_conflict_heartbeat_takeover_release(mysql_test_engine):
    factory = sessionmaker(bind=mysql_test_engine, future=True)
    first, second = factory(), factory()
    now = datetime.now(UTC)
    a, b = Repository(first), Repository(second)
    assert a.acquire_system_lease(lease_name="r2-matrix", owner_id="owner-a", host="test", pid=1, now=now)
    assert not b.acquire_system_lease(lease_name="r2-matrix", owner_id="owner-b", host="test", pid=2, now=now + timedelta(seconds=10))
    assert a.heartbeat_system_lease(lease_name="r2-matrix", owner_id="owner-a", now=now + timedelta(seconds=20))
    assert not b.heartbeat_system_lease(lease_name="r2-matrix", owner_id="owner-b", now=now + timedelta(seconds=20))
    assert b.acquire_system_lease(lease_name="r2-matrix", owner_id="owner-b", host="test", pid=2, now=now + timedelta(seconds=200))
    assert b.release_system_lease(lease_name="r2-matrix", owner_id="owner-b")
    first.close(); second.close()


def test_mysql_duplicate_claim_and_transaction_rollback(mysql_test_engine):
    factory = sessionmaker(bind=mysql_test_engine, future=True)
    seed, first, second = factory(), factory(), factory()
    now = datetime.now(UTC)
    seed.add(PublicationJob(job_type="RECONCILE_PUBLICATION", entity_id="r2-claim", idempotency_key="r2-claim-key", not_before=now - timedelta(seconds=1), attempt_number=0, max_attempts=3, status="PENDING"))
    seed.commit(); seed.close()
    a, b = Repository(first), Repository(second)
    claimed_a = a.claim_due_publication_job(now)
    claimed_b = b.claim_due_publication_job(now)
    assert claimed_a is not None
    assert claimed_b is None
    assert a.complete_publication_job(claimed_a.id, success=True).status == "SUCCEEDED"
    assert a.claim_due_publication_job(now) is None
    assert a.acquire_system_lease(lease_name="r2-rollback", owner_id="owner", host="test", pid=1, now=now)
    first.close(); second.close()


def test_mysql_stale_reconcile_metrics_and_retry_state_recovery(mysql_test_engine):
    session = sessionmaker(bind=mysql_test_engine, future=True)()
    repo = Repository(session)
    now = datetime.now(UTC)
    pub = publication(state="SUCCEEDED", request_id="r2-stale-success")
    unknown = publication(state="UNKNOWN_PLATFORM_STATE", request_id="r2-stale-unknown", post_id=None)
    session.add_all([pub, unknown])
    old = now - timedelta(minutes=10)
    session.add_all([
        PublicationJob(job_type="RECONCILE_PUBLICATION", entity_id="r2-reconcile", idempotency_key="r2-reconcile-key", not_before=old, attempt_number=1, max_attempts=3, status="RUNNING", updated_at=old),
        PublicationJob(job_type="INGEST_POST_METRICS", entity_id="r2-metrics:15_MIN", idempotency_key="r2-metrics-key", not_before=old, attempt_number=1, max_attempts=3, status="RUNNING", analytics_window="15_MIN", updated_at=old),
        PublicationJob(job_type="RETRY_PUBLICATION", entity_id="r2-stale-success", idempotency_key="r2-retry-success-key", not_before=old, attempt_number=1, max_attempts=3, status="RUNNING", updated_at=old),
        PublicationJob(job_type="RETRY_PUBLICATION", entity_id="r2-stale-unknown", idempotency_key="r2-retry-unknown-key", not_before=old, attempt_number=1, max_attempts=3, status="RUNNING", updated_at=old),
    ])
    session.commit()
    recovered = repo.recover_stale_publication_jobs(now)
    assert len(recovered) == 4
    scoped = [row for row in session.query(PublicationJob).all() if row.idempotency_key in {"r2-reconcile-key", "r2-metrics-key", "r2-retry-success-key", "r2-retry-unknown-key"}]
    rows = {row.id: row.status for row in scoped}
    assert len(rows) == 4
    assert "FAILED_RETRYABLE" in rows.values()
    assert sum(value == "SUCCEEDED" for value in rows.values()) == 1
    assert sum(value == "FAILED_FINAL" for value in rows.values()) == 1
    session.close()


def test_mysql_metrics_snapshot_capture_key_and_four_windows(mysql_test_engine):
    session = sessionmaker(bind=mysql_test_engine, future=True)()
    repo = Repository(session)
    now = datetime.now(UTC)
    pub = SimpleNamespace(platform_post_id="r2-metrics-post", publication_request_id="r2-metrics-request", platform="X")
    jobs = repo.ensure_metrics_jobs(pub, now=now)
    assert len(jobs) == 4
    assert len(repo.ensure_metrics_jobs(pub, now=now)) == 4
    assert session.query(PublicationJob).filter(PublicationJob.entity_id.like("r2-metrics-post:%")).count() == 4
    snapshot = PostMetricsSnapshot(capture_key="r2-metrics-post:15_MIN:2026-07-14T00:00:00+00:00", platform="X", platform_post_id="r2-metrics-post", publication_request_id="r2-metrics-request", captured_at=now, analytics_window="15_MIN", impressions=0, likes=0, replies=0, reposts=0, bookmarks=0, engagement_rate=None, source_provider="fake", provider_payload_version="r2", availability_flags_json={"impressions": True})
    first = repo.save_metrics_snapshot(snapshot)
    second = repo.save_metrics_snapshot(snapshot)
    assert first.id == second.id
    assert session.query(PostMetricsSnapshot).count() == 1
    session.close()


def test_mysql_cycle_hold_and_summary_aggregation(mysql_test_engine):
    session = sessionmaker(bind=mysql_test_engine, future=True)()
    repo = Repository(session)
    now = datetime.now(UTC)
    cycle = SimpleNamespace(cycle_id="r2-summary-cycle", runner_id="r2", analysis_id="r2", session_date=now.date().isoformat(), market_state="OPEN", started_at=now, completed_at=now, analysis_confidence=85, opportunity_count=1, selected_candidate_count=1, draft_count=1, approved_draft_count=1, publication_decision="DRY_RUN", publication_request_id=None, outcome="SAFE_NO_PUBLICATION", error_category=None)
    repo.save_production_cycle(cycle, operating_mode="DRY_RUN")
    repo.upsert_global_hold(state="AUTH_HOLD", reason="matrix")
    assert repo.get_global_hold().hold_state == "AUTH_HOLD"
    repo.upsert_global_hold(state="CLEAR", reason="matrix cleared", cleared_at=now)
    summary = repo.get_session_summary(now.date().isoformat())
    assert summary["cycles_completed"] == 1
    assert summary["opportunities_detected"] == 1
    assert summary["active_hold"] == "CLEAR"
    session.close()
