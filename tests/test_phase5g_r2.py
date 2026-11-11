from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as N

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.content.drafting import ContentDraftingService, FakeDraftingProvider
from alpha_market_engine.content.opportunities import ContentOpportunityEngine
from alpha_market_engine.content.publication import ContentPublicationService, FakePublishingProvider
from alpha_market_engine.database.base import Base
from alpha_market_engine.database.repository import Repository
from alpha_market_engine.market.brain_types import DivergenceSignal, EventLifecycle
from alpha_market_engine.production import AlphaTradingFloorOrchestrator, GlobalPublicationHold, HoldState, LimitedAutoPolicy, OperatingMode, ProductionSafetyService


NOW = datetime(2026, 7, 14, 16, 0, tzinfo=UTC)


class ActiveCalendar:
    def session_for(self, now):
        return (now.date(), now - timedelta(hours=1), now + timedelta(hours=1))
    def is_active(self, now):
        return True


def market_result(*, quiet=False):
    events = []
    signal = None
    if not quiet:
        signal = DivergenceSignal.QQQ_OUTPERFORMING_SPY
    return N(
        analysis_id="r2-analysis",
        analysis_started_at=NOW,
        analysis_session_date="2026-07-14",
        analysis_confidence=N(score=50 if quiet else 85),
        price_action={"SPY": N(events=events)},
        divergences=N(signals=[signal] if signal else [], confidence=50 if quiet else 85, relative_performance_facts={"QQQ": {"relative_difference": "0.75"}} if signal else {}, index_spread_fact=None),
        regime=N(regime=N(value="ROTATION" if signal else "RANGE"), confidence=50 if quiet else 85, supporting_data={}, scores={"ROTATION": 50 if quiet else 85}),
        observations=[], structures={}, momentum={}
    )


def runtime_objects(*, quiet=False, publication_provider=None, auto=False):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    repo = Repository(session)
    drafting_provider = FakeDraftingProvider()
    drafting = ContentDraftingService(drafting_provider, repository=repo)
    publication_provider = publication_provider or FakePublishingProvider()
    publication = ContentPublicationService(publication_provider, repository=repo, auto_post_enabled=auto)
    async def analyze_market():
        return market_result(quiet=quiet)
    orchestrator = AlphaTradingFloorOrchestrator(
        market_brain=N(analyze_market=analyze_market),
        opportunity_engine=ContentOpportunityEngine(repository=repo),
        drafting_service=drafting,
        publication_service=publication,
        repository=repo,
        calendar=ActiveCalendar(),
        policy=LimitedAutoPolicy(enabled=auto, minimum_draft_quality=80),
        hold=GlobalPublicationHold(repository=repo),
        runner_id="r2-test",
    )
    return repo, session, engine, drafting_provider, publication_provider, orchestrator


def test_injected_full_graph_uses_real_opportunity_drafting_validator_and_dry_run():
    import asyncio
    repo, session, engine, drafting, publishing, orchestrator = runtime_objects()
    result = asyncio.run(orchestrator.run_cycle(now=NOW, mode=OperatingMode.DRY_RUN))
    assert result.selected_candidate_count >= 1
    assert drafting.calls >= 1
    assert publishing.invocation_count == 0
    assert result.outcome == "SAFE_NO_PUBLICATION"
    session.close(); engine.dispose()


def test_injected_zero_candidate_has_no_draft_or_publish_calls():
    import asyncio
    repo, session, engine, drafting, publishing, orchestrator = runtime_objects(quiet=True)
    result = asyncio.run(orchestrator.run_cycle(now=NOW, mode=OperatingMode.DRY_RUN))
    assert result.outcome == "HEALTHY_NO_CANDIDATE"
    assert drafting.calls == 0
    assert publishing.invocation_count == 0
    session.close(); engine.dispose()


def test_limited_auto_fake_provider_path_is_service_only():
    import asyncio
    repo, session, engine, drafting, publishing, orchestrator = runtime_objects(auto=True)
    orchestrator.limited_auto_ready = lambda: True
    result = asyncio.run(orchestrator.run_cycle(now=NOW, mode=OperatingMode.LIMITED_AUTO))
    assert publishing.invocation_count == 1
    assert result.publication_decision == "SUCCEEDED"
    session.close(); engine.dispose()


def test_worker_has_no_direct_x_provider_calls():
    import inspect
    from alpha_market_engine.production import PublicationJobWorker
    source = inspect.getsource(PublicationJobWorker)
    assert "XApiPublishingProvider" not in source
    assert "XReconciliationProvider" not in source
    assert "self.metrics_provider.get_metrics" not in source


def test_worker_metrics_complete_and_duplicate_snapshot_contract():
    from types import SimpleNamespace
    from alpha_market_engine.content.observability import FakeMetricsProvider, ProviderMetricsResult
    from alpha_market_engine.production import PublicationJobWorker
    provider = FakeMetricsProvider([ProviderMetricsResult(True, "fake", "m1", impressions=100, likes=4, replies=2, reposts=3, bookmarks=1, availability_flags={"impressions": True, "likes": True, "replies": True, "reposts": True, "bookmarks": True})])
    class Repo:
        def __init__(self): self.job = SimpleNamespace(id=11, job_type="INGEST_POST_METRICS", entity_id="post-1:15_MIN", analytics_window="15_MIN", attempt_number=0, max_attempts=3); self.snapshots=[]; self.completed=None
        def claim_due_publication_job(self, now, stale_after_seconds): job, self.job = self.job, None; return job
        def get_publication_by_request_id(self, key): return None
        def get_publication_by_platform_post_id(self, key): return SimpleNamespace(state="SUCCEEDED", platform_post_id="post-1", platform="X", publication_request_id="req-1")
        def save_metrics_snapshot(self, snapshot): self.snapshots.append(snapshot); return snapshot
        def add_system_event_sync(self, **kwargs): pass
        def complete_publication_job(self, job_id, **kwargs): self.completed=kwargs
    repo=Repo(); worker=PublicationJobWorker(repo, metrics_provider=provider)
    result=worker.run_once(now=NOW)
    assert result.status == "SUCCEEDED" and provider.invocation_count == 1
    assert repo.snapshots[0].engagement_rate is not None


def test_worker_metrics_retryable_provider_schedules_retry():
    from types import SimpleNamespace
    from alpha_market_engine.content.observability import FakeMetricsProvider, ProviderMetricsResult
    from alpha_market_engine.production import PublicationJobWorker
    provider = FakeMetricsProvider([ProviderMetricsResult(False, "fake", "m-retry", retryable=True, error_category="TIMEOUT")])
    class Repo:
        def __init__(self): self.job=SimpleNamespace(id=13,job_type="INGEST_POST_METRICS",entity_id="post-3:15_MIN",analytics_window="15_MIN",attempt_number=0,max_attempts=3); self.completed=None
        def claim_due_publication_job(self, now, stale_after_seconds): job,self.job=self.job,None; return job
        def get_publication_by_request_id(self,key): return None
        def get_publication_by_platform_post_id(self,key): return SimpleNamespace(state="SUCCEEDED",platform_post_id="post-3",platform="X",publication_request_id="req-3")
        def add_system_event_sync(self,**kwargs): pass
        def complete_publication_job(self,job_id,**kwargs): self.completed=kwargs
    repo=Repo(); result=PublicationJobWorker(repo, metrics_provider=provider).run_once(now=NOW)
    assert result.status == "FAILED_RETRYABLE"
    assert repo.completed["retryable"] is True and repo.completed["not_before"] > NOW


def test_worker_metrics_permanent_failure_is_final():
    from types import SimpleNamespace
    from alpha_market_engine.content.observability import FakeMetricsProvider, ProviderMetricsResult
    from alpha_market_engine.production import PublicationJobWorker
    provider = FakeMetricsProvider([ProviderMetricsResult(False, "fake", "m1", error_category="POST_NOT_FOUND")])
    class Repo:
        def __init__(self): self.job=SimpleNamespace(id=12,job_type="INGEST_POST_METRICS",entity_id="post-2:1_HOUR",analytics_window="1_HOUR",attempt_number=0,max_attempts=3); self.completed=None
        def claim_due_publication_job(self, now, stale_after_seconds): job,self.job=self.job,None; return job
        def get_publication_by_request_id(self,key): return None
        def get_publication_by_platform_post_id(self,key): return SimpleNamespace(state="SUCCEEDED",platform_post_id="post-2",platform="X",publication_request_id="req-2")
        def add_system_event_sync(self,**kwargs): pass
        def complete_publication_job(self,job_id,**kwargs): self.completed=kwargs
    repo=Repo(); result=PublicationJobWorker(repo, metrics_provider=provider).run_once(now=NOW)
    assert result.status == "FAILED_FINAL" and repo.completed["retryable"] is False
def test_noop_coalescing_twelve_cycles_and_session_summary():
    from alpha_market_engine.production import CycleRecord
    repo, session, engine, *_ = runtime_objects(quiet=True)
    base = datetime.now(UTC)
    for index in range(12):
        stamp = base + timedelta(minutes=5 * index)
        record = CycleRecord(f"noop-{index}", "r2", base.date().isoformat(), stamp, stamp, "OPEN", "quiet", 50, 0, 0, 0, 0, None, None, "HEALTHY_NO_CANDIDATE")
        repo.maybe_emit_no_op_event(session_date=record.session_date, now=stamp, selected_candidate_count=0)
        repo.save_production_cycle(record, operating_mode="DRY_RUN")
    events = repo.get_system_events("NO_FRESH_OPPORTUNITY")
    assert len(events) <= 2
    summary = repo.get_session_summary(base.date().isoformat())
    assert summary["cycles_completed"] == 12
    assert summary["healthy_no_op_cycles"] == 12
    assert "metrics_snapshots" in summary
    session.close(); engine.dispose()


def test_safety_hold_transitions_and_clear_requirements():
    repo, session, engine, *_ = runtime_objects()
    safety = ProductionSafetyService(repo)
    safety.auth_failure("REFRESH_UNAVAILABLE")
    assert repo.get_global_hold().hold_state == HoldState.AUTH_HOLD.value
    try:
        safety.clear_auth(verified=False, expected_username=True, scopes=True)
        assert False
    except RuntimeError:
        pass
    safety.clear_auth(verified=True, expected_username=True, scopes=True)
    assert repo.get_global_hold().hold_state == HoldState.CLEAR.value
    safety.unknown_platform_state("r2-publication")
    assert repo.get_global_hold().hold_state == HoldState.PLATFORM_STATE_HOLD.value
    try:
        safety.clear_platform(1)
        assert False
    except RuntimeError:
        pass
    safety.clear_platform(0)
    assert repo.get_global_hold().hold_state == HoldState.CLEAR.value
    session.close(); engine.dispose()
