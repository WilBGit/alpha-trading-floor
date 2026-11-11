from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.content.publication import ContentPublicationService, ProviderPublicationResult, PublishMode
from alpha_market_engine.database.models import PublicationJob, PublicationRecord
from alpha_market_engine.database.repository import Repository
from alpha_market_engine.production import HoldState, PublicationJobWorker, ProductionSafetyService


def _clean(engine):
    assert engine.url.database == "alpha_market_test", f"destructive certification refused database={engine.url.database!r}"
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in ("publication_jobs", "publication_records", "publication_holds"):
            connection.execute(text(f"TRUNCATE TABLE `{table}`"))
        connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))


def _draft(key):
    now = datetime.now(UTC)
    return SimpleNamespace(draft_id=f"r4-sh-draft-{key}", draft_request_id=f"r4-sh-request-{key}", analysis_id=f"r4-sh-analysis-{key}", opportunity_id=f"r4-sh-opp-{key}", opportunity_key=f"QQQ_LEADERSHIP-{key}", platform="X", content_text="$QQQ is leading $SPY on relative strength.", content_hash=f"r4-sh-hash-{key}", provider="fake", model="fake", prompt_version="r4", voice_profile="default", draft_attempt=1, generated_at=now, character_count=51, validation_status="VALID", validation_failures=(), factual_claims=(), numeric_claims=(), symbol_claims=("QQQ", "SPY"), quality_score=100, final_decision="APPROVE", final_decision_reasons=())


def _opp(key):
    return SimpleNamespace(opportunity_id=f"r4-sh-opp-{key}", opportunity_key=f"QQQ_LEADERSHIP-{key}", opportunity_type="QQQ_LEADERSHIP", content_pattern_key="QQQ_LEADERSHIP", confirmation_state="CONFIRMED", decision="POST_CANDIDATE", status="GENERATED", detected_at=datetime.now(UTC))


class Provider:
    def __init__(self, result): self.result = result; self.calls = 0
    def publish(self, request): self.calls += 1; return self.result


def test_shutdown_before_provider_request_is_recoverable_without_unknown_state(mysql_test_engine):
    _clean(mysql_test_engine); session = sessionmaker(bind=mysql_test_engine, future=True)(); repo = Repository(session); ready = Event(); release = Event(); provider = Provider(ProviderPublicationResult(True, "fake", "never", 200, None))
    def before(_request):
        ready.set(); release.wait(timeout=10); raise RuntimeError("shutdown before provider dispatch")
    service = ContentPublicationService(provider, repository=repo, auto_post_enabled=True, before_provider_hook=before)
    errors = []
    thread = Thread(target=lambda: _run_capture(service, errors), daemon=True); thread.start(); assert ready.wait(10); release.set(); thread.join(10)
    row = repo.get_publication_by_idempotency_key(repo.get_publication_records(limit=1)[0].idempotency_key)
    assert errors and provider.calls == 0 and row.state == "SENDING"
    session.close(); mysql_test_engine.dispose()


def _run_capture(service, errors):
    try: service.publish(_draft("before"), _opp("before"), mode=PublishMode.LIMITED_AUTO, approval_status="APPROVE")
    except Exception as exc: errors.append(exc)


def test_shutdown_after_known_provider_success_recovery_does_not_republish(mysql_test_engine):
    _clean(mysql_test_engine); session = sessionmaker(bind=mysql_test_engine, future=True)(); repo = Repository(session); ready = Event(); release = Event(); provider = Provider(ProviderPublicationResult(True, "fake", "known-post", 200, None))
    def after(_request, _result):
        ready.set(); release.wait(timeout=10); raise RuntimeError("shutdown before local persistence completion")
    service = ContentPublicationService(provider, repository=repo, auto_post_enabled=True, after_provider_success_hook=after)
    errors = []; thread = Thread(target=lambda: _run_capture(service, errors), daemon=True); thread.start(); assert ready.wait(10); release.set(); thread.join(10)
    row = repo.get_publication_records(limit=1)[0]; assert errors and provider.calls == 1 and row.state == "SENDING"
    now = datetime.now(UTC); session.add(PublicationJob(job_type="RETRY_PUBLICATION", entity_id=row.idempotency_key, idempotency_key="r4-sh-retry-known", not_before=now - timedelta(seconds=1), attempt_number=0, max_attempts=3, status="PENDING")); session.commit()
    recovery_provider = Provider(ProviderPublicationResult(True, "fake", "duplicate", 200, None)); recovery = ContentPublicationService(recovery_provider, repository=repo, auto_post_enabled=True); result = PublicationJobWorker(repo, publication_service=recovery).run_once(now=now)
    assert recovery_provider.calls == 0 and repo.get_global_hold().hold_state == HoldState.PLATFORM_STATE_HOLD.value and result.status == "FAILED_FINAL"
    session.close(); mysql_test_engine.dispose()


def test_shutdown_unknown_response_remains_single_request_and_reconciliation_bound(mysql_test_engine):
    _clean(mysql_test_engine); session = sessionmaker(bind=mysql_test_engine, future=True)(); repo = Repository(session); provider = Provider(ProviderPublicationResult(False, "fake", "ambiguous", 202, None, transmitted=True, error_category="AMBIGUOUS_RESPONSE")); service = ContentPublicationService(provider, repository=repo, auto_post_enabled=True); result = service.publish(_draft("unknown"), _opp("unknown"), mode=PublishMode.LIMITED_AUTO, approval_status="APPROVE")
    row = repo.get_publication_records(limit=1)[0]; assert result.status.value == "UNKNOWN_PLATFORM_STATE" and provider.calls == 1 and row.state == "UNKNOWN_PLATFORM_STATE"; ProductionSafetyService(repo).unknown_platform_state(row.publication_request_id); assert repo.get_global_hold().hold_state == HoldState.PLATFORM_STATE_HOLD.value
    session.close(); mysql_test_engine.dispose()
