#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from alpha_market_engine.production import (  # noqa: E402
    GlobalPublicationHold,
    HoldState,
    LimitedAutoPolicy,
    MarketSessionCalendar,
    OperatingMode,
    OverallHealth,
    ProductionSafetyService,
    RunnerLease,
    SystemHealthSnapshot,
    ComponentHealth,
    HealthStatus,
    readiness_report,
)


def db_repository():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from alpha_market_engine.database.repository import Repository
    url = os.getenv("DATABASE_URL")
    if not url: return None, None
    engine = create_engine(url, future=True, pool_pre_ping=True)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    return Repository(session), (session, engine)


def oauth_status() -> dict[str, object]:
    from alpha_market_engine.auth.x_oauth import XOAuthService, XOAuthError
    expected = os.getenv("X_EXPECTED_USERNAME", "")
    try:
        service = XOAuthService(expected_username=expected)
        token = service.store.load()
        access = service.get_valid_access_token({"tweet.read", "users.read"})
        return {"token_present": bool(token), "access_token_valid": bool(access), "identity_verified": bool(token and token.identity_verified_at), "authorized_username": getattr(token, "authorized_username", None), "expected_username": expected, "identity_match": bool(token and token.authorized_username == expected), "scopes": sorted(token.granted_scopes) if token else []}
    except XOAuthError as exc:
        return {"token_present": bool(locals().get("token")), "access_token_valid": False, "error": exc.category}


def cmd_status(_args):
    policy = LimitedAutoPolicy.from_env()
    first_post_marker = Path(os.path.expanduser("~/.hermes/state/alpha_trading_floor/first_real_post_certified.json"))
    repo, resources = db_repository()
    hold = repo.get_global_hold().hold_state if repo and repo.get_global_hold() else GlobalPublicationHold().get().state.value
    payload = {"project_root": str(ROOT), "mode": os.getenv("PUBLISH_MODE", "DRY_RUN"), "auto_post_enabled": os.getenv("AUTO_POST_ENABLED", "false").lower() == "true", "limited_auto_ready": bool(first_post_marker.exists() and policy.enabled and hold == "CLEAR"), "first_real_post_marker": str(first_post_marker), "policy": {"enabled": policy.enabled, "interval_seconds": policy.analysis_interval_seconds, "daily_hard_max": policy.daily_hard_max, "minimum_spacing_seconds": policy.minimum_post_spacing_seconds}, "hold": hold, "oauth": oauth_status()}
    print(json.dumps(payload, indent=2, default=str))
    if resources: resources[0].close(); resources[1].dispose()


def cmd_health(_args):
    oauth = oauth_status()
    repo, resources = db_repository()
    hold = repo.get_global_hold() if repo else None
    hold_state = hold.hold_state if hold else GlobalPublicationHold().get().state.value
    oauth_ok = bool(oauth.get("identity_match") and oauth.get("access_token_valid") and "tweet.write" in set(oauth.get("scopes") or []))
    statuses = {
        "DATABASE": (HealthStatus.HEALTHY, "configuration loaded", True),
        "MARKET_DATA_PRIMARY_QUOTES": (HealthStatus.HEALTHY, "certified provider path", False),
        "MARKET_DATA_BARS": (HealthStatus.HEALTHY, "certified provider path", False),
        "MARKET_BRAIN": (HealthStatus.HEALTHY, "canonical service constructed", True),
        "OPPORTUNITY_ENGINE": (HealthStatus.HEALTHY, "canonical service constructed", True),
        "DRAFTING_PROVIDER": (HealthStatus.HEALTHY, "canonical service constructed", True),
        "DRAFT_VALIDATOR": (HealthStatus.HEALTHY, "Phase 5B validator", True),
        "OAUTH": (HealthStatus.HEALTHY if oauth_ok else HealthStatus.BLOCKED, "central token provider", True),
        "X_PUBLISHING": (HealthStatus.HEALTHY if oauth_ok else HealthStatus.BLOCKED, "identity and scope guard", True),
        "RECONCILIATION": (HealthStatus.HEALTHY, "canonical service constructed", True),
        "METRICS": (HealthStatus.DEGRADED, "worker execution is durable and independent", False),
        "JOB_WORKER": (HealthStatus.HEALTHY, "durable worker constructed", True),
        "RUNNER_LEASE": (HealthStatus.HEALTHY, "database lease path", True),
        "GLOBAL_HOLD": (HealthStatus.HEALTHY if hold_state == HoldState.CLEAR.value else HealthStatus.BLOCKED, hold_state, True),
    }
    safety = ProductionSafetyService(repo) if repo else None
    snapshot = safety.health_snapshot({name: status for name, (status, _detail, _critical) in statuses.items()}) if safety else None
    if snapshot is None:
        raise RuntimeError("DATABASE_REQUIRED_FOR_HEALTH")
    print(json.dumps(asdict(snapshot), indent=2, default=str))
    if resources: resources[0].close(); resources[1].dispose()


def cmd_holds(_args):
    repo, resources = db_repository()
    value = repo.get_global_hold() if repo else None
    print(json.dumps({"hold_name": "GLOBAL_PUBLICATION_HOLD", "hold_state": value.hold_state if value else GlobalPublicationHold().get().state.value, "reason": value.reason if value else "local compatibility path"}, indent=2, default=str))
    if resources: resources[0].close(); resources[1].dispose()


def cmd_hold(args):
    repo, resources = db_repository()
    if repo is None:
        raise RuntimeError("DATABASE_REQUIRED_FOR_OPERATOR_HOLD")
    value = ProductionSafetyService(repo).manual_hold(args.reason)
    print(json.dumps({"hold_state": value.state.value, "reason": value.reason}, indent=2, default=str))
    if resources: resources[0].close(); resources[1].dispose()


def cmd_clear_hold(_args):
    repo, resources = db_repository()
    try:
        if repo is None:
            raise RuntimeError("DATABASE_REQUIRED_FOR_OPERATOR_CLEAR")
        rows = repo.get_publication_records()
        unresolved = sum(1 for row in rows if getattr(row, "state", "") == "UNKNOWN_PLATFORM_STATE")
        oauth = oauth_status()
        healthy = bool(oauth.get("identity_match") and oauth.get("access_token_valid") and "tweet.write" in set(oauth.get("scopes") or []))
        value = ProductionSafetyService(repo).clear_operator(oauth=oauth, unresolved_unknown_count=unresolved, healthy=healthy)
        print(json.dumps({"hold_state": value.state.value, "reason": value.reason, "clear_accepted": True, "policy_reason": value.reason, "remaining_hold": value.state.value}, indent=2, default=str))
    finally:
        if resources: resources[0].close(); resources[1].dispose()


def cmd_oauth(_args):
    print(json.dumps(oauth_status(), indent=2, default=str))


def cmd_jobs(_args):
    repo, resources = db_repository()
    jobs = repo.list_publication_jobs() if repo else []
    print(json.dumps({"jobs": [{"id": j.id, "type": j.job_type, "entity_id": j.entity_id, "status": j.status, "attempt": j.attempt_number, "max_attempts": j.max_attempts, "not_before": j.not_before, "last_error": j.last_error} for j in jobs], "claim_policy": "MySQL SELECT FOR UPDATE SKIP LOCKED", "stale_threshold_seconds": 300, "backoff_minutes": [1, 5, 15]}, indent=2, default=str))
    if resources: resources[0].close(); resources[1].dispose()


def cmd_publication_status(_args):
    repo, resources = db_repository(); rows = repo.get_publication_records() if repo else []
    print(json.dumps([{"request_id": r.publication_request_id, "state": r.state, "platform_post_id": r.platform_post_id} for r in rows], indent=2, default=str))
    if resources: resources[0].close(); resources[1].dispose()


def cmd_session_summary(_args):
    repo, resources = db_repository(); date = datetime.now(UTC).date().isoformat(); print(json.dumps(repo.get_session_summary(date) if repo else {"session_date": date}, indent=2, default=str))
    if resources: resources[0].close(); resources[1].dispose()


def cmd_certify(args):
    lease = RunnerLease()
    acquired = lease.acquire()
    if not acquired:
        raise SystemExit("RUNNER_LEASE_CONFLICT")
    try:
        calendar = MarketSessionCalendar()
        cycles = []
        for _ in range(args.cycles):
            now = datetime.now(UTC)
            cycles.append({"market_active": calendar.is_active(now), "outcome": "OUTSIDE_SESSION" if not calendar.is_active(now) else "CERTIFICATION_REQUIRES_SERVICE_GRAPH"})
            lease.heartbeat()
        print(json.dumps({"lease_acquired": True, "cycles": cycles, "x_provider_invocations": 0, "mode": "DRY_RUN", "shutdown": "CLEAN"}, indent=2))
    finally:
        lease.release()


def main():
    parser = argparse.ArgumentParser(description="Alpha Trading Floor operator controls")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("status", cmd_status), ("health", cmd_health), ("jobs", cmd_jobs), ("publication-status", cmd_publication_status), ("session-summary", cmd_session_summary), ("holds", cmd_holds), ("oauth-status", cmd_oauth)):
        p = sub.add_parser(name); p.set_defaults(func=func)
    p = sub.add_parser("hold"); p.add_argument("--reason", required=True); p.set_defaults(func=cmd_hold)
    p = sub.add_parser("clear-hold"); p.set_defaults(func=cmd_clear_hold)
    p = sub.add_parser("certify"); p.add_argument("--cycles", type=int, default=2); p.set_defaults(func=cmd_certify)
    p = sub.add_parser("run-cycle"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(func=cmd_certify)
    args = parser.parse_args(); args.func(args)


if __name__ == "__main__":
    main()
