from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import signal
import socket
import time
import uuid
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from datetime import UTC, date, datetime, time as dt_time, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

from alpha_market_engine.content.publication import PublishMode

PACIFIC = ZoneInfo("America/Los_Angeles")
STATE_DIR = Path(os.path.expanduser("~/.hermes/state/alpha_trading_floor"))


class OperatingMode(str, Enum):
    OFF = "OFF"
    DRY_RUN = "DRY_RUN"
    MANUAL = "MANUAL"
    LIMITED_AUTO = "LIMITED_AUTO"


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"


class OverallHealth(str, Enum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"
    STOPPED = "STOPPED"


class HoldState(str, Enum):
    CLEAR = "CLEAR"
    MANUAL_HOLD = "MANUAL_HOLD"
    AUTH_HOLD = "AUTH_HOLD"
    PLATFORM_STATE_HOLD = "PLATFORM_STATE_HOLD"
    DATABASE_HOLD = "DATABASE_HOLD"
    SYSTEM_HEALTH_HOLD = "SYSTEM_HEALTH_HOLD"


@dataclass(frozen=True, slots=True)
class LimitedAutoPolicy:
    enabled: bool = False
    market_days_only: bool = True
    session_start: dt_time = dt_time(6, 30)
    session_end: dt_time = dt_time(12, 55)
    analysis_interval_seconds: int = 300
    maximum_post_candidates_per_cycle: int = 1
    minimum_post_spacing_seconds: int = 720
    daily_soft_target_min: int = 6
    daily_soft_target_max: int = 8
    daily_hard_max: int = 10
    require_confirmed_opportunity: bool = True
    minimum_opportunity_confidence: int = 0
    minimum_content_score: int = 0
    minimum_draft_quality: int = 80
    require_exact_duplicate_clear: bool = True
    require_near_duplicate_clear: bool = True
    require_oauth_identity_match: bool = True
    require_refresh_capability: bool = True
    stop_on_auth_failure: bool = True
    stop_on_unknown_platform_state: bool = True
    stop_on_database_failure: bool = True
    stop_on_repeated_provider_failure: bool = True
    repeated_provider_failure_threshold: int = 3

    @classmethod
    def from_env(cls) -> "LimitedAutoPolicy":
        return cls(
            enabled=os.getenv("AUTO_POST_ENABLED", "false").strip().lower() == "true" and os.getenv("PUBLISH_MODE", "DRY_RUN").upper() == OperatingMode.LIMITED_AUTO.value,
            analysis_interval_seconds=int(os.getenv("ALPHA_ENGINE_INTERVAL_SECONDS", "300")),
            daily_hard_max=int(os.getenv("ALPHA_DAILY_HARD_MAX", "10")),
            minimum_post_spacing_seconds=int(os.getenv("ALPHA_MIN_POST_SPACING_SECONDS", "720")),
        )

    @property
    def publish_mode(self) -> PublishMode:
        mode = os.getenv("PUBLISH_MODE", "DRY_RUN").upper()
        if mode == OperatingMode.MANUAL.value:
            return PublishMode.MANUAL_APPROVAL
        if mode == OperatingMode.LIMITED_AUTO.value:
            return PublishMode.LIMITED_AUTO
        return PublishMode.DRY_RUN


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    component: str
    status: HealthStatus
    detail: str = ""
    publication_critical: bool = False
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class SystemHealthSnapshot:
    overall: OverallHealth
    components: tuple[ComponentHealth, ...]
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def publication_ready(self) -> bool:
        return self.overall == OverallHealth.READY and all(not c.publication_critical or c.status == HealthStatus.HEALTHY for c in self.components)


@dataclass(frozen=True, slots=True)
class HoldRecord:
    state: HoldState
    reason: str
    created_at: datetime
    updated_at: datetime
    cleared_at: datetime | None = None
    source_reference: str | None = None


@dataclass(frozen=True, slots=True)
class CycleRecord:
    cycle_id: str
    runner_id: str
    session_date: str
    started_at: datetime
    completed_at: datetime | None
    market_state: str
    analysis_id: str | None
    analysis_confidence: int | None
    opportunity_count: int
    selected_candidate_count: int
    draft_count: int
    approved_draft_count: int
    publication_decision: str | None
    publication_request_id: str | None
    outcome: str
    error_category: str | None = None


class JsonStateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self, default: Any = None) -> Any:
        if not self.path.exists():
            return default
        return json.loads(self.path.read_text())

    def save(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=_json_default) + "\n")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        os.chmod(self.path, 0o600)


class GlobalPublicationHold:
    def __init__(self, store: JsonStateStore | None = None, repository: Any | None = None):
        self.store = store or JsonStateStore(STATE_DIR / "global_hold.json")
        self.repository = repository

    def get(self) -> HoldRecord:
        if self.repository is not None:
            row = self.repository.get_global_hold()
            if row is not None:
                return HoldRecord(HoldState(row.hold_state), row.reason, _aware(row.created_at) or datetime.now(UTC), _aware(row.updated_at) or datetime.now(UTC), _aware(row.cleared_at), row.source_reference)
        raw = self.store.load()
        if not raw:
            now = datetime.now(UTC)
            return HoldRecord(HoldState.CLEAR, "", now, now)
        return HoldRecord(HoldState(raw["state"]), raw["reason"], _parse_dt(raw["created_at"]), _parse_dt(raw["updated_at"]), _parse_dt(raw.get("cleared_at")), raw.get("source_reference"))

    def set(self, state: HoldState, reason: str, source_reference: str | None = None) -> HoldRecord:
        now = datetime.now(UTC)
        current = self.get()
        record = HoldRecord(state, reason, current.created_at if current.state != HoldState.CLEAR else now, now, None, source_reference)
        if self.repository is not None:
            self.repository.upsert_global_hold(state=state.value, reason=reason, source_reference=source_reference)
            return record
        self.store.save(asdict(record))
        return record

    def clear(self, *, reason: str = "operator cleared") -> HoldRecord:
        now = datetime.now(UTC)
        record = HoldRecord(HoldState.CLEAR, reason, now, now, now)
        if self.repository is not None:
            self.repository.upsert_global_hold(state=HoldState.CLEAR.value, reason=reason, cleared_at=now)
            return record
        self.store.save(asdict(record))
        return record


class MarketSessionCalendar:
    def __init__(self, exchange: str = "XNYS", timezone: ZoneInfo = PACIFIC):
        self.calendar = xcals.get_calendar(exchange)
        self.timezone = timezone

    def session_for(self, now: datetime) -> tuple[date, datetime, datetime] | None:
        local = now.astimezone(self.timezone)
        session = local.date().isoformat()
        if not self.calendar.is_session(session):
            return None
        open_utc = self.calendar.session_open(session).to_pydatetime().astimezone(UTC)
        close_utc = self.calendar.session_close(session).to_pydatetime().astimezone(UTC)
        normal_start = datetime.combine(local.date(), dt_time(6, 30), self.timezone).astimezone(UTC)
        normal_end = datetime.combine(local.date(), dt_time(12, 55), self.timezone).astimezone(UTC)
        return local.date(), max(open_utc, normal_start), min(close_utc, normal_end)

    def is_active(self, now: datetime) -> bool:
        session = self.session_for(now)
        if session is None:
            return False
        _, start, end = session
        return start <= now.astimezone(UTC) <= end


class RunnerLease:
    def __init__(self, name: str = "alpha_trading_floor_production_runner", path: str | Path | None = None, ttl_seconds: int = 900):
        self.name = name
        self.path = Path(path or STATE_DIR / f"{name}.lock")
        self.ttl_seconds = ttl_seconds
        self._handle = None
        self.owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._handle.close(); self._handle = None
            return False
        self._handle.seek(0); self._handle.truncate()
        self._handle.write(json.dumps({"lease_name": self.name, "owner_id": self.owner_id, "host": socket.gethostname(), "pid": os.getpid(), "heartbeat_at": datetime.now(UTC).isoformat()})); self._handle.flush()
        return True

    def heartbeat(self) -> None:
        if self._handle is None:
            raise RuntimeError("runner lease not held")
        self._handle.seek(0); self._handle.truncate(); self._handle.write(json.dumps({"lease_name": self.name, "owner_id": self.owner_id, "host": socket.gethostname(), "pid": os.getpid(), "heartbeat_at": datetime.now(UTC).isoformat()})); self._handle.flush()

    def release(self) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN); self._handle.close(); self._handle = None


class DatabaseRunnerLease:
    def __init__(self, repository: Any, *, lease_name: str = "alpha_trading_floor_production_runner", heartbeat_seconds: int = 30, ttl_seconds: int = 120):
        self.repository = repository
        self.lease_name = lease_name
        self.heartbeat_seconds = heartbeat_seconds
        self.ttl_seconds = ttl_seconds
        self.owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"

    def acquire(self, now: datetime | None = None) -> bool:
        return self.repository.acquire_system_lease(lease_name=self.lease_name, owner_id=self.owner_id, host=socket.gethostname(), pid=os.getpid(), now=now or datetime.now(UTC), ttl_seconds=self.ttl_seconds)

    def heartbeat(self, now: datetime | None = None) -> bool:
        return self.repository.heartbeat_system_lease(lease_name=self.lease_name, owner_id=self.owner_id, now=now or datetime.now(UTC), ttl_seconds=self.ttl_seconds)

    def release(self) -> bool:
        return self.repository.release_system_lease(lease_name=self.lease_name, owner_id=self.owner_id)


class ProductionSafetyService:
    def __init__(self, repository: Any, hold: GlobalPublicationHold | None = None):
        self.repository = repository
        self.hold = hold or GlobalPublicationHold(repository=repository)
        self.database_available = True
        self.runtime_hold_state: HoldState | None = None
        self.last_database_error = ""

    def auth_failure(self, category: str, detail: str = "") -> HoldRecord:
        return self.hold.set(HoldState.AUTH_HOLD, f"{category}: {detail}".strip())

    def unknown_platform_state(self, publication_request_id: str) -> HoldRecord:
        return self.hold.set(HoldState.PLATFORM_STATE_HOLD, "unresolved unknown platform state", publication_request_id)

    def database_failure(self, detail: str = "") -> HoldRecord:
        return self.hold.set(HoldState.DATABASE_HOLD, f"database persistence failure: {detail}".strip())

    def repeated_provider_failure(self, provider: str, count: int) -> HoldRecord | None:
        if count < 3: return None
        return self.hold.set(HoldState.SYSTEM_HEALTH_HOLD, f"three consecutive {provider} provider failures")

    def _record_failure(self, event_type: str, provider: str, detail: str = "", window_minutes: int = 60) -> HoldRecord | None:
        now = datetime.now(UTC)
        self.repository.add_system_event_sync(
            event_type=event_type,
            severity="ERROR",
            component=provider,
            message=f"{provider} terminal provider failure",
            metadata={"detail": detail[:256], "window_minutes": window_minutes, "failure_at": now.isoformat()},
        )
        cutoff = now - timedelta(minutes=window_minutes)
        events = self.repository.get_system_events(event_type)
        recent = []
        for event in events:
            failure_at = (getattr(event, "metadata_json", None) or {}).get("failure_at")
            if failure_at:
                try:
                    timestamp = datetime.fromisoformat(failure_at).astimezone(UTC)
                except ValueError:
                    continue
                if timestamp >= cutoff:
                    recent.append(event)
        return self.repeated_provider_failure(provider, len(recent))

    def record_drafting_failure(self, detail: str = "") -> HoldRecord | None:
        return self._record_failure("DRAFTING_PROVIDER_FINAL_FAILURE", "DRAFTING", detail)

    def record_x_provider_failure(self, detail: str = "") -> HoldRecord | None:
        return self._record_failure("X_PROVIDER_FINAL_FAILURE", "X_PUBLISHING", detail)

    def mark_database_unavailable(self, detail: str = "") -> None:
        self.database_available = False
        self.runtime_hold_state = HoldState.DATABASE_HOLD
        self.last_database_error = detail[:256]

    def mark_database_available(self) -> HoldRecord:
        self.database_available = True
        return self.hold.set(HoldState.DATABASE_HOLD, "database recovered; explicit clear required")

    def manual_hold(self, reason: str) -> HoldRecord:
        return self.hold.set(HoldState.MANUAL_HOLD, reason)

    def clear_database(self, *, healthy: bool) -> HoldRecord:
        if not healthy:
            raise RuntimeError("DATABASE_STILL_UNAVAILABLE")
        return self.hold.clear(reason="operator cleared after database recovery")

    def clear_operator(self, *, oauth: dict[str, Any], unresolved_unknown_count: int, healthy: bool) -> HoldRecord:
        state = self.hold.get().state
        if state == HoldState.AUTH_HOLD:
            return self.clear_auth(verified=bool(oauth.get("identity_verified")), expected_username=bool(oauth.get("identity_match")), scopes=bool(oauth.get("scopes")))
        if state == HoldState.PLATFORM_STATE_HOLD:
            return self.clear_platform(unresolved_unknown_count)
        if state == HoldState.DATABASE_HOLD:
            return self.clear_database(healthy=healthy)
        if state == HoldState.SYSTEM_HEALTH_HOLD:
            return self.clear_system(healthy)
        return self.hold.clear(reason="operator cleared manual hold")

    def clear_auth(self, *, verified: bool, expected_username: bool, scopes: bool) -> HoldRecord:
        if not (verified and expected_username and scopes):
            raise RuntimeError("AUTH_CLEAR_REQUIRES_VERIFIED_IDENTITY")
        if self.hold.get().state != HoldState.AUTH_HOLD:
            return self.hold.get()
        return self.hold.clear(reason="operator cleared after verified OAuth identity")

    def clear_platform(self, unresolved_unknown_count: int) -> HoldRecord:
        if unresolved_unknown_count: raise RuntimeError("PLATFORM_STATE_UNRESOLVED")
        return self.hold.clear(reason="operator cleared after reconciliation")

    def clear_system(self, healthy: bool) -> HoldRecord:
        if not healthy: raise RuntimeError("SYSTEM_HEALTH_STILL_FAILING")
        return self.hold.clear(reason="operator cleared after health recovery")

    def health_snapshot(self, statuses: dict[str, HealthStatus], *, checked_at: datetime | None = None) -> SystemHealthSnapshot:
        critical = {"DATABASE", "MARKET_BRAIN", "OPPORTUNITY_ENGINE", "DRAFTING_PROVIDER", "DRAFT_VALIDATOR", "OAUTH", "X_PUBLISHING", "RECONCILIATION", "JOB_WORKER", "RUNNER_LEASE", "GLOBAL_HOLD"}
        components = tuple(ComponentHealth(name, status, publication_critical=name in critical, checked_at=checked_at or datetime.now(UTC)) for name, status in statuses.items())
        hold = self.hold.get().state
        if hold != HoldState.CLEAR: overall = OverallHealth.NOT_READY
        elif any(c.status in {HealthStatus.UNAVAILABLE, HealthStatus.BLOCKED} for c in components if c.publication_critical): overall = OverallHealth.NOT_READY
        elif any(c.status == HealthStatus.DEGRADED for c in components): overall = OverallHealth.DEGRADED
        else: overall = OverallHealth.READY
        return SystemHealthSnapshot(overall, components, checked_at or datetime.now(UTC))


class AlphaTradingFloorOrchestrator:
    def __init__(self, *, market_brain: Any, opportunity_engine: Any, drafting_service: Any, publication_service: Any, repository: Any | None = None, calendar: MarketSessionCalendar | None = None, policy: LimitedAutoPolicy | None = None, hold: GlobalPublicationHold | None = None, runner_id: str | None = None):
        self.market_brain = market_brain
        self.opportunity_engine = opportunity_engine
        self.drafting_service = drafting_service
        self.publication_service = publication_service
        self.repository = repository
        self.calendar = calendar or MarketSessionCalendar()
        self.policy = policy or LimitedAutoPolicy.from_env()
        self.hold = hold or GlobalPublicationHold(repository=repository)
        self.runner_id = runner_id or str(uuid.uuid4())
        self.cycles = JsonStateStore(STATE_DIR / "cycles.json")
        self._current_mode = OperatingMode.DRY_RUN.value

    def limited_auto_ready(self) -> bool:
        marker = Path(os.path.expanduser("~/.hermes/state/alpha_trading_floor/first_real_post_certified.json"))
        return self.policy.enabled and marker.exists() and self.hold.get().state == HoldState.CLEAR

    async def run_cycle(self, *, now: datetime | None = None, mode: OperatingMode = OperatingMode.DRY_RUN) -> CycleRecord:
        now = now or datetime.now(UTC)
        self._current_mode = mode.value
        session = self.calendar.session_for(now)
        started = now
        cycle_id = str(uuid.uuid4())
        if session is None or not self.calendar.is_active(now):
            return self._record(CycleRecord(cycle_id, self.runner_id, now.astimezone(PACIFIC).date().isoformat(), started, datetime.now(UTC), "CLOSED", None, None, 0, 0, 0, 0, None, None, "OUTSIDE_SESSION"))
        if mode == OperatingMode.OFF:
            return self._record(CycleRecord(cycle_id, self.runner_id, session[0].isoformat(), started, datetime.now(UTC), "OPEN", None, None, 0, 0, 0, 0, None, None, "OFF"))
        if mode == OperatingMode.LIMITED_AUTO and not self.limited_auto_ready():
            return self._record(CycleRecord(cycle_id, self.runner_id, session[0].isoformat() if session else now.astimezone(PACIFIC).date().isoformat(), started, datetime.now(UTC), "OPEN", None, None, 0, 0, 0, 0, "BLOCKED_READINESS", None, "LIMITED_AUTO_NOT_READY"))
        if self.hold.get().state != HoldState.CLEAR and mode == OperatingMode.LIMITED_AUTO:
            return self._record(CycleRecord(cycle_id, self.runner_id, session[0].isoformat(), started, datetime.now(UTC), "OPEN", None, None, 0, 0, 0, 0, "BLOCKED_HOLD", None, "BLOCKED"))
        try:
            analysis = await self.market_brain.analyze_market()
            opportunities = self.opportunity_engine.evaluate(analysis)
            selected = list(getattr(opportunities, "selected_candidates", []) or [])[: self.policy.maximum_post_candidates_per_cycle]
            if not selected:
                return self._record(CycleRecord(cycle_id, self.runner_id, session[0].isoformat(), started, datetime.now(UTC), "OPEN", getattr(analysis, "analysis_id", None), getattr(getattr(analysis, "analysis_confidence", None), "score", None), len(getattr(opportunities, "detected_opportunities", []) or []), 0, 0, 0, None, None, "HEALTHY_NO_CANDIDATE"))
            drafts = []
            approved = []
            for opportunity in selected:
                result = self.drafting_service.draft(opportunity)
                drafts.append(result)
                if getattr(result, "approved_draft", None) is not None:
                    approved.append(result.approved_draft)
            if not approved:
                return self._record(CycleRecord(cycle_id, self.runner_id, session[0].isoformat(), started, datetime.now(UTC), "OPEN", getattr(analysis, "analysis_id", None), getattr(getattr(analysis, "analysis_confidence", None), "score", None), len(getattr(opportunities, "detected_opportunities", []) or []), len(selected), len(drafts), 0, "BLOCKED_DRAFT", None, "DRAFT_REJECTED"))
            if mode in {OperatingMode.DRY_RUN, OperatingMode.MANUAL}:
                return self._record(CycleRecord(cycle_id, self.runner_id, session[0].isoformat(), started, datetime.now(UTC), "OPEN", getattr(analysis, "analysis_id", None), getattr(getattr(analysis, "analysis_confidence", None), "score", None), len(getattr(opportunities, "detected_opportunities", []) or []), len(selected), len(drafts), len(approved), "DRY_RUN" if mode == OperatingMode.DRY_RUN else "MANUAL_APPROVAL_REQUIRED", None, "SAFE_NO_PUBLICATION"))
            result = self.publication_service.publish(approved[0], selected[0], PublishMode.LIMITED_AUTO if mode == OperatingMode.LIMITED_AUTO else PublishMode.DRY_RUN, now=now, approval_status="APPROVED")
            return self._record(CycleRecord(cycle_id, self.runner_id, session[0].isoformat(), started, datetime.now(UTC), "OPEN", getattr(analysis, "analysis_id", None), getattr(getattr(analysis, "analysis_confidence", None), "score", None), len(getattr(opportunities, "detected_opportunities", []) or []), len(selected), len(drafts), len(approved), getattr(getattr(result, "status", None), "value", str(getattr(result, "status", None))), getattr(result, "publication_request_id", None), "PUBLISHED" if getattr(getattr(result, "status", None), "value", None) == "SUCCEEDED" else "PUBLICATION_RESULT"))
        except Exception as exc:
            return self._record(CycleRecord(cycle_id, self.runner_id, session[0].isoformat(), started, datetime.now(UTC), "OPEN", None, None, 0, 0, 0, 0, None, None, "FAILED", type(exc).__name__))

    def _record(self, record: CycleRecord) -> CycleRecord:
        if self.repository is not None and hasattr(self.repository, "save_production_cycle"):
            if record.outcome in {"HEALTHY_NO_CANDIDATE", "HEALTHY_NO_OPPORTUNITY"} and hasattr(self.repository, "maybe_emit_no_op_event"):
                self.repository.maybe_emit_no_op_event(session_date=record.session_date, now=record.completed_at or record.started_at, selected_candidate_count=record.selected_candidate_count)
            self.repository.save_production_cycle(record, operating_mode=self._current_mode)
        rows = self.cycles.load([]) or []
        rows.append(asdict(record)); self.cycles.save(rows[-500:]); return record


@dataclass(frozen=True, slots=True)
class JobExecutionResult:
    job_id: int
    job_type: str
    status: str
    error: str | None = None


class PublicationJobWorker:
    """Repository-backed worker for reconciliation, retry, and metrics jobs."""
    def __init__(self, repository: Any, handlers: dict[str, Callable[[Any], Any]] | None = None, *, stale_after_seconds: int = 300, reconciliation_service: Any | None = None, publication_service: Any | None = None, metrics_provider: Any | None = None, metrics_service: Any | None = None, oauth: Any | None = None, certification: bool = False, safety: Any | None = None):
        self.repository = repository
        self.handlers = handlers or {}
        self.stale_after_seconds = stale_after_seconds
        self.reconciliation_service = reconciliation_service
        self.publication_service = publication_service
        self.oauth = oauth
        self.safety = safety
        from alpha_market_engine.content.observability import MetricsIngestionService
        self.metrics_service = metrics_service or (MetricsIngestionService(metrics_provider, repository=repository, safety=safety) if metrics_provider is not None else None)
        self.certification = certification

    def _reconcile(self, job):
        publication = self.repository.get_publication_by_request_id(job.entity_id)
        if publication is None:
            raise RuntimeError("PUBLICATION_NOT_FOUND")
        result = self.reconciliation_service.reconcile(publication, force=True)
        state = getattr(result.reconciliation_state, "value", result.reconciliation_state)
        if state == "PLATFORM_POST_FOUND":
            self.repository.ensure_metrics_jobs(publication)
        elif state == "PLATFORM_STATE_AMBIGUOUS":
            self.repository.upsert_global_hold(state="PLATFORM_STATE_HOLD", reason="publication platform state is ambiguous", source_reference=publication.publication_request_id)
        elif state == "PLATFORM_POST_NOT_FOUND" and result.retry_allowed:
            from alpha_market_engine.content.observability import JobStatus, JobType, PublicationJobSpec, job_idempotency_key
            entity = publication.idempotency_key
            self.repository.schedule_publication_job(PublicationJobSpec(JobType.RETRY_PUBLICATION, entity, job_idempotency_key(JobType.RETRY_PUBLICATION, entity), job.not_before, max_attempts=3, status=JobStatus.PENDING))
        return result

    def _retry(self, job):
        publication = self.repository.get_publication_by_idempotency_key(job.entity_id)
        if publication is None:
            raise RuntimeError("PUBLICATION_NOT_FOUND")
        if publication.state == "SUCCEEDED":
            return "SUCCEEDED_ALREADY"
        if publication.state in {"UNKNOWN_PLATFORM_STATE", "SENDING"}:
            self.repository.upsert_global_hold(state="PLATFORM_STATE_HOLD", reason="publication retry requires reconciliation", source_reference=publication.publication_request_id)
            raise RuntimeError("RECONCILIATION_REQUIRED")
        draft_row = self.repository.get_generated_content_draft_by_id(publication.draft_id)
        opportunity_row = self.repository.get_content_opportunity_by_id(publication.opportunity_id)
        if draft_row is None or opportunity_row is None:
            raise RuntimeError("PUBLICATION_CONTEXT_NOT_FOUND")
        from alpha_market_engine.content.drafting import GeneratedContentDraft
        draft = GeneratedContentDraft(draft_row.draft_id, draft_row.draft_request_id, draft_row.analysis_id, draft_row.opportunity_id, draft_row.opportunity_key, draft_row.platform, draft_row.content_text, draft_row.content_hash, draft_row.provider, draft_row.model, draft_row.prompt_version, draft_row.voice_profile, draft_row.draft_attempt, draft_row.generated_at, draft_row.character_count, draft_row.validation_status, tuple(draft_row.validation_failures_json or ()), tuple(draft_row.factual_claims_json or ()), tuple(draft_row.numeric_claims_json or ()), tuple(draft_row.symbol_claims_json or ()), draft_row.quality_score, draft_row.final_decision, tuple(draft_row.final_decision_reasons_json or ()))
        opportunity = SimpleNamespace(**({column.name: getattr(opportunity_row, column.name) for column in opportunity_row.__table__.columns} if hasattr(opportunity_row, "__table__") else vars(opportunity_row)))
        result = self.publication_service.publish(draft, opportunity, mode=PublishMode(publication.publish_mode), now=datetime.now(UTC), approval_status=publication.approval_status, successful_posts=self.repository.get_successful_publications(publication.platform), idempotency_key=publication.idempotency_key)
        if getattr(result.status, "value", result.status) == "UNKNOWN_PLATFORM_STATE":
            self.repository.upsert_global_hold(state="PLATFORM_STATE_HOLD", reason="publication retry returned ambiguous platform state", source_reference=publication.publication_request_id)
        return result

    def _metrics(self, job):
        publication_key = job.entity_id.split(":", 1)[0]
        publication = self.repository.get_publication_by_request_id(publication_key)
        if publication is None and hasattr(self.repository, "get_publication_by_platform_post_id"):
            publication = self.repository.get_publication_by_platform_post_id(publication_key)
        if publication is None or publication.state != "SUCCEEDED" or not publication.platform_post_id:
            raise RuntimeError("PUBLICATION_NOT_SUCCEEDED")
        window = job.analytics_window or (job.entity_id.split(":", 1)[1] if ":" in job.entity_id else "UNKNOWN")
        snapshot = self.metrics_service.ingest(platform_post_id=publication.platform_post_id, platform=publication.platform, publication_request_id=publication.publication_request_id, analytics_window=window, captured_at=datetime.now(UTC))
        if snapshot is None:
            result = self.metrics_service.last_result
            raise RuntimeError((result.error_category if result is not None else "METRICS_UNAVAILABLE") or "METRICS_UNAVAILABLE")
        return snapshot

    def run_once(self, now: datetime | None = None) -> JobExecutionResult | None:
        current = now or datetime.now(UTC)
        if hasattr(self.repository, "recover_stale_publication_jobs"):
            self.repository.recover_stale_publication_jobs(current, stale_after_seconds=self.stale_after_seconds)
        job = self.repository.claim_due_publication_job(current, stale_after_seconds=self.stale_after_seconds)
        if job is None:
            return None
        try:
            handler = self.handlers.get(job.job_type) or {"RECONCILE_PUBLICATION": self._reconcile, "RETRY_PUBLICATION": self._retry, "INGEST_POST_METRICS": self._metrics}.get(job.job_type)
            if handler is None:
                raise RuntimeError("UNSUPPORTED_JOB_TYPE")
            handler(job)
            self.repository.complete_publication_job(job.id, success=True)
            return JobExecutionResult(job.id, job.job_type, "SUCCEEDED")
        except Exception as exc:
            metrics_retryable = job.job_type == "INGEST_POST_METRICS" and bool(getattr(self.metrics_service.last_result, "retryable", False))
            retryable = (job.job_type in {"RECONCILE_PUBLICATION", "INGEST_POST_METRICS"} and int(job.attempt_number or 0) < int(job.max_attempts or 0) and (job.job_type != "INGEST_POST_METRICS" or metrics_retryable))
            delays = {"RECONCILE_PUBLICATION": (60, 300, 900), "INGEST_POST_METRICS": (300, 900, 1800), "RETRY_PUBLICATION": (60, 300)}
            delay = delays.get(job.job_type, (60,))[min(max(int(job.attempt_number or 1) - 1, 0), len(delays.get(job.job_type, (60,))) - 1)]
            next_time = (now or datetime.now(UTC)) + timedelta(seconds=delay)
            self.repository.complete_publication_job(job.id, success=False, retryable=retryable, error=type(exc).__name__, not_before=next_time)
            if job.job_type == "RETRY_PUBLICATION" and type(exc).__name__ == "RuntimeError" and str(exc) == "RECONCILIATION_REQUIRED":
                self.repository.upsert_global_hold(state="PLATFORM_STATE_HOLD", reason="publication retry requires reconciliation", source_reference=job.entity_id)
            return JobExecutionResult(job.id, job.job_type, "FAILED_RETRYABLE" if retryable else "FAILED_FINAL", type(exc).__name__)

    def run_due_jobs(self, max_jobs: int = 10, now: datetime | None = None) -> list[JobExecutionResult]:
        results = []
        for _ in range(max_jobs):
            result = self.run_once(now=now)
            if result is None: break
            results.append(result)
        return results


def readiness_report(*, source_control: bool, database: bool, schema: bool, oauth: bool, identity: bool, first_real_post: bool, worker: bool, lease: bool, hold: HoldState, calendar: bool = True) -> dict[str, Any]:
    gates = {"SOURCE CONTROL": source_control, "DATABASE": database, "SCHEMA": schema, "MARKET CALENDAR": calendar, "OAUTH": oauth, "EXPECTED ACCOUNT": identity, "DURABLE JOB WORKER": worker, "RUNNER LEASE": lease, "GLOBAL HOLD": hold == HoldState.CLEAR, "FIRST REAL POST": first_real_post}
    return {"gates": {name: "PASS" if value else "BLOCKED" for name, value in gates.items()}, "limited_auto_ready": all(gates.values())}


def _aware(value: datetime | None) -> datetime | None:
    if value is None: return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _parse_dt(value: str | None) -> datetime | None:
    if not value: return None
    parsed = datetime.fromisoformat(value); return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime): return value.isoformat()
    if isinstance(value, Enum): return value.value
    if hasattr(value, "__dict__"): return value.__dict__
    return str(value)
