from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.content.observability import (
    FakeMetricsProvider,
    FakeReconciliationProvider,
    JobType,
    MetricsIngestionService,
    ProviderMetricsResult,
    ProviderReconciliationResult,
    PublicationJobSpec,
    PublicationReconciliationService,
    summarize_metrics,
    job_idempotency_key,
)
from alpha_market_engine.content.publication import PublicationState
from alpha_market_engine.database.models import PublicationRecord
from alpha_market_engine.database.repository import Repository


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 5D publication observability certification")
    parser.add_argument("--certify", action="store_true", required=True)
    args = parser.parse_args()
    if os.getenv("AUTO_POST_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        raise SystemExit("Refusing certification while AUTO_POST_ENABLED is enabled")
    if not args.certify:
        return 2

    now = datetime.now(UTC)
    request_id = f"phase5d-cert-{uuid4()}"
    idempotency_key = f"phase5d-cert-idem-{uuid4()}"
    content = "$QQQ +1.11% vs $SPY +0.36%. Clear relative strength from $QQQ."
    content_hash = __import__("hashlib").sha256(content.encode()).hexdigest()
    engine = create_engine(os.environ["DATABASE_URL"])
    session = sessionmaker(bind=engine)()
    repo = Repository(session)
    record = PublicationRecord(
        publication_request_id=request_id,
        idempotency_key=idempotency_key,
        analysis_id=f"phase5d-analysis-{uuid4()}",
        opportunity_id=f"phase5d-opportunity-{uuid4()}",
        opportunity_key="PHASE5D_CERTIFICATION_QQQ",
        draft_id=f"phase5d-draft-{uuid4()}",
        draft_request_id=f"phase5d-draft-request-{uuid4()}",
        platform="X",
        content_text=content,
        content_hash=content_hash,
        content_pattern_key="QQQ_SPY:RELATIVE_PERFORMANCE:QQQ",
        publish_mode="DRY_RUN",
        approval_status="APPROVED",
        decision="DRY_RUN_ELIGIBLE",
        decision_reasons_json=[],
        state=PublicationState.UNKNOWN_PLATFORM_STATE.value,
        attempt_number=1,
        provider="certification-fake",
        provider_request_id="cert-send-1",
        requested_at=now - timedelta(minutes=6),
        attempted_at=now - timedelta(minutes=6),
        source_detected_at=now - timedelta(minutes=6),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    repo.schedule_publication_job(PublicationJobSpec(JobType.RECONCILE_PUBLICATION, request_id, job_idempotency_key(JobType.RECONCILE_PUBLICATION, request_id), now))

    fake_reconciliation = FakeReconciliationProvider([ProviderReconciliationResult(True, "fake-reconciliation", "cert-reconcile-1", "cert-post-1", content_hash, content, now - timedelta(minutes=6), "exact_content_hash", 100)])
    reconciliation = PublicationReconciliationService(fake_reconciliation, repository=repo).reconcile(record, now=now)
    metrics_provider = FakeMetricsProvider([ProviderMetricsResult(True, "fake-metrics", "cert-metrics-1", impressions=100, likes=4, replies=2, reposts=3, bookmarks=1, availability_flags={"impressions": True, "likes": True, "replies": True, "reposts": True, "bookmarks": True})])
    metrics = MetricsIngestionService(metrics_provider, repository=repo).ingest(platform_post_id="cert-post-1", publication_request_id=request_id, analytics_window="15_MIN", captured_at=now)
    summary = summarize_metrics([metrics], group_key="QQQ_SPY:RELATIVE_PERFORMANCE:QQQ", analytics_window="15_MIN") if metrics else None
    repo.schedule_publication_job(PublicationJobSpec(JobType.INGEST_POST_METRICS, request_id, job_idempotency_key(JobType.INGEST_POST_METRICS, request_id), now))
    print(json.dumps({
        "certification": True,
        "synthetic": True,
        "publish_provider_invocations": 0,
        "publication_request_id": request_id,
        "original_state": PublicationState.UNKNOWN_PLATFORM_STATE.value,
        "reconciliation_state": reconciliation.reconciliation_state.value,
        "platform_post_found": reconciliation.platform_post_found,
        "platform_post_id": reconciliation.platform_post_id,
        "metrics_snapshot": {"capture_key": metrics.capture_key, "engagement_rate": str(metrics.engagement_rate), "analytics_window": metrics.analytics_window} if metrics else None,
        "content_pattern_key": record.content_pattern_key,
        "opportunity_id": record.opportunity_id,
        "analysis_id": record.analysis_id,
        "performance_summary": {"post_count": summary.post_count, "average_impressions": str(summary.average_impressions), "average_engagement_rate": str(summary.average_engagement_rate)} if summary else None,
        "sample_size_label": summary.sample_size_label if summary else None,
        "auto_post_enabled": False,
        "real_x_post_published": False,
    }, indent=2))
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
