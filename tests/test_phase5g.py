import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from alpha_market_engine.production import (
    GlobalPublicationHold, HoldState, JsonStateStore, LimitedAutoPolicy,
    MarketSessionCalendar, OperatingMode, PublicationJobWorker, RunnerLease,
    readiness_report,
)


def test_policy_safe_defaults(monkeypatch):
    monkeypatch.delenv("AUTO_POST_ENABLED", raising=False)
    monkeypatch.delenv("PUBLISH_MODE", raising=False)
    policy = LimitedAutoPolicy.from_env()
    assert policy.enabled is False
    assert policy.daily_hard_max == 10
    assert policy.minimum_post_spacing_seconds == 720


def test_limited_auto_requires_explicit_pair(monkeypatch):
    monkeypatch.setenv("AUTO_POST_ENABLED", "true")
    monkeypatch.setenv("PUBLISH_MODE", "DRY_RUN")
    assert LimitedAutoPolicy.from_env().enabled is False
    monkeypatch.setenv("PUBLISH_MODE", "LIMITED_AUTO")
    assert LimitedAutoPolicy.from_env().enabled is True


def test_hold_persists_and_clear_is_explicit(tmp_path):
    hold = GlobalPublicationHold(JsonStateStore(tmp_path / "hold.json"))
    assert hold.get().state == HoldState.CLEAR
    assert hold.set(HoldState.AUTH_HOLD, "identity mismatch").state == HoldState.AUTH_HOLD
    assert hold.get().state == HoldState.AUTH_HOLD
    assert hold.clear().state == HoldState.CLEAR


def test_runner_lease_conflict_and_release(tmp_path):
    first = RunnerLease(path=tmp_path / "runner.lock")
    second = RunnerLease(path=tmp_path / "runner.lock")
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_calendar_holiday_is_closed():
    calendar = MarketSessionCalendar()
    assert calendar.session_for(datetime(2026, 7, 4, 15, tzinfo=UTC)) is None


def test_readiness_is_blocked_without_first_post():
    result = readiness_report(source_control=True, database=True, schema=True, oauth=True, identity=True, first_real_post=False, worker=True, lease=True, hold=HoldState.CLEAR)
    assert result["limited_auto_ready"] is False
    assert result["gates"]["FIRST REAL POST"] == "BLOCKED"


def test_worker_claims_once_and_completes():
    class Repo:
        def __init__(self): self.job = SimpleNamespace(id=1, job_type="RECONCILE_PUBLICATION", attempt_number=0, max_attempts=3); self.done = None
        def claim_due_publication_job(self, now, stale_after_seconds):
            if self.job is None: return None
            job, self.job = self.job, None; return job
        def complete_publication_job(self, job_id, **kwargs): self.done = (job_id, kwargs)
    repo = Repo(); calls=[]
    worker = PublicationJobWorker(repo, {"RECONCILE_PUBLICATION": lambda job: calls.append(job.id)})
    first = worker.run_once()
    assert first is not None and first.status == "SUCCEEDED"
    assert worker.run_once() is None
    assert calls == [1]
    assert repo.done[1]["success"] is True
