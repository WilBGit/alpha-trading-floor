from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.content.drafting import GeneratedContentDraft
from alpha_market_engine.content.publication import ContentPublicationService, ProviderPublicationResult
from alpha_market_engine.database.models import ContentOpportunity, GeneratedContentDraft as DraftRow, PublicationJob, PublicationRecord
from alpha_market_engine.database.repository import Repository
from alpha_market_engine.production import PublicationJobWorker


def _db(engine):
    assert engine.url.database == "alpha_market_test", f"destructive certification refused database={engine.url.database!r}"
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in ("publication_jobs", "publication_records", "generated_content_drafts", "content_opportunities"):
            connection.execute(text(f"TRUNCATE TABLE `{table}`"))
        connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))


def _seed(engine, *, key: str, prior_success: bool = False):
    session = sessionmaker(bind=engine, future=True)(); now = datetime.now(UTC)
    opp = ContentOpportunity(detected_at=now, opportunity_type="QQQ_LEADERSHIP", symbols_json=["QQQ", "SPY"], title="QQQ leadership", observation="QQQ is outperforming SPY", supporting_data_json={}, importance_score=90, time_sensitivity_score=90, status="GENERATED", opportunity_id=f"r4-opp-{key}", opportunity_key=f"QQQ_LEADERSHIP-{key}", analysis_id=f"r4-analysis-{key}", primary_symbol="QQQ", decision="POST_CANDIDATE", decision_reasons_json=[], content_pattern_key="QQQ_LEADERSHIP", confirmation_state="CONFIRMED")
    draft = DraftRow(draft_id=f"r4-draft-{key}", draft_request_id=f"r4-request-{key}", analysis_id=f"r4-analysis-{key}", opportunity_id=opp.opportunity_id, opportunity_key=opp.opportunity_key, platform="X", content_text="$QQQ is leading $SPY on relative strength.", content_hash=f"r4-hash-{key}", provider="fake", model="fake", prompt_version="r4", voice_profile="default", draft_attempt=1, generated_at=now, character_count=51, validation_status="VALID", validation_failures_json=[], factual_claims_json=[], numeric_claims_json=[], symbol_claims_json=["QQQ", "SPY"], quality_score=100, final_decision="APPROVE", final_decision_reasons_json=[])
    publication = PublicationRecord(publication_request_id=f"r4-pub-{key}", idempotency_key=f"r4-idem-{key}", analysis_id=opp.analysis_id, opportunity_id=opp.opportunity_id, opportunity_key=opp.opportunity_key, draft_id=draft.draft_id, draft_request_id=draft.draft_request_id, platform="X", content_text=draft.content_text, content_hash=draft.content_hash, content_pattern_key="QQQ_LEADERSHIP", publish_mode="LIMITED_AUTO", approval_status="APPROVE", decision="APPROVE", decision_reasons_json=[], state="FAILED_RETRYABLE", attempt_number=1, requested_at=now, source_detected_at=now)
    session.add_all([opp, draft, publication])
    if prior_success:
        session.add(PublicationRecord(publication_request_id="r4-prior", idempotency_key="r4-prior-idem", analysis_id="prior", opportunity_id="prior-opp", opportunity_key="prior", draft_id="prior-draft", draft_request_id="prior-request", platform="X", content_text="prior", content_hash="prior-hash", content_pattern_key="prior", publish_mode="LIMITED_AUTO", approval_status="APPROVE", decision="APPROVE", decision_reasons_json=[], state="SUCCEEDED", attempt_number=1, platform_post_id="prior-post", requested_at=now - timedelta(minutes=1), published_at=now - timedelta(minutes=1), source_detected_at=now - timedelta(minutes=1)))
    job = PublicationJob(job_type="RETRY_PUBLICATION", entity_id=publication.idempotency_key, idempotency_key=f"r4-job-{key}", not_before=now - timedelta(seconds=1), attempt_number=0, max_attempts=3, status="PENDING")
    session.add(job); session.commit(); session.close()


class Provider:
    def __init__(self): self.calls = 0
    def publish(self, request):
        self.calls += 1
        return ProviderPublicationResult(True, "fake", f"r4-retry-post-{self.calls}", 200, None, error_category=None, sanitized_error=None)


def _worker(engine, provider, *, daily_hard_max=5):
    session = sessionmaker(bind=engine, future=True)(); repo = Repository(session)
    service = ContentPublicationService(provider, repository=repo, auto_post_enabled=True, daily_hard_max=daily_hard_max)
    return session, repo, PublicationJobWorker(repo, reconciliation_service=None, publication_service=service)


def test_retry_publication_still_eligible_reconstructs_persisted_context(mysql_test_engine):
    _db(mysql_test_engine); _seed(mysql_test_engine, key="eligible")
    provider = Provider(); session, repo, worker = _worker(mysql_test_engine, provider)
    result = worker.run_once(now=datetime.now(UTC))
    assert result.status == "SUCCEEDED" and provider.calls == 1
    assert repo.get_publication_by_idempotency_key("r4-idem-eligible").state == "SUCCEEDED"
    assert session.query(PublicationRecord).filter_by(idempotency_key="r4-idem-eligible").count() == 1
    session.close(); mysql_test_engine.dispose()


def test_retry_publication_no_longer_eligible_routes_to_service_without_provider(mysql_test_engine):
    _db(mysql_test_engine); _seed(mysql_test_engine, key="blocked", prior_success=True)
    provider = Provider(); session, repo, worker = _worker(mysql_test_engine, provider, daily_hard_max=1)
    result = worker.run_once(now=datetime.now(UTC))
    assert result.status == "SUCCEEDED" and provider.calls == 0
    assert repo.get_publication_by_idempotency_key("r4-idem-blocked").state == "BLOCKED"
    session.close(); mysql_test_engine.dispose()
