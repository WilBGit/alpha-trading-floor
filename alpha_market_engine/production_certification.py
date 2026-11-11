from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class CertificationResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EXECUTED = "NOT_EXECUTED"


@dataclass(frozen=True, slots=True)
class CertificationScenario:
    scenario_id: str
    required_result: CertificationResult = CertificationResult.PASS


@dataclass(frozen=True, slots=True)
class CertificationEvidence:
    scenario_id: str
    executed: bool
    actual_result: CertificationResult
    evidence: str
    observed_result: str


REQUIRED_CERTIFICATION_SCENARIOS: tuple[CertificationScenario, ...] = tuple(
    CertificationScenario(name)
    for name in (
        "EARLY_CLOSE_BOUNDARY", "AUTH_FAILURE_HOLD", "OAUTH_REFRESH_FAILURE_HOLD", "IDENTITY_MISMATCH_HOLD", "REQUIRED_SCOPE_FAILURE_HOLD", "DATABASE_FAIL_CLOSED",
        "DRAFTING_BREAKER_RESTART_DURABILITY", "X_PROVIDER_BREAKER_RESTART_DURABILITY", "UNKNOWN_PUBLICATION_HOLD", "RECONCILIATION_HOLD_CLEAR_REJECTION", "RECONCILIATION_HOLD_CLEAR_SUCCESS", "HOLD_CLEAR_FUTURE_ELIGIBILITY",
        "STALE_RECONCILIATION_RECOVERY", "STALE_METRICS_RECOVERY", "STALE_RETRY_SUCCEEDED", "STALE_RETRY_UNKNOWN", "RETRY_PUBLICATION_ELIGIBLE", "RETRY_PUBLICATION_NO_LONGER_ELIGIBLE",
        "METRICS_COMPLETE", "METRICS_PARTIAL", "METRICS_ZERO", "METRICS_POST_NOT_FOUND", "METRICS_RETRYABLE", "METRICS_PERMANENT_FAILURE", "METRICS_DUPLICATE_CAPTURE",
        "DUPLICATE_JOB_CLAIM", "STALE_LEASE_TAKEOVER", "SESSION_SUMMARY_MYSQL", "ROLLBACK_PRODUCTION_CYCLE", "ROLLBACK_DURABLE_JOB", "ROLLBACK_PUBLICATION_RETRY", "ROLLBACK_METRICS_INGESTION",
        "SHUTDOWN_BEFORE_PROVIDER_REQUEST", "SHUTDOWN_AFTER_KNOWN_PROVIDER_SUCCESS", "SHUTDOWN_UNKNOWN_PROVIDER_RESPONSE", "NO_OP_COALESCING", "FULL_DRY_RUN_SERVICE_GRAPH", "ZERO_CANDIDATE_SERVICE_GRAPH",
        "TEST_LIMITED_AUTO_FAKE_PROVIDER", "REAL_CLOCK_DRY_RUN", "RUNNER_LEASE", "CLI_HEALTH", "OPERATOR_HOLD_CLEAR",
    )
)


class CertificationMatrix:
    def __init__(self, evidence: Iterable[CertificationEvidence] = ()) -> None:
        self._evidence = {item.scenario_id: item for item in evidence}

    def record(self, item: CertificationEvidence) -> None:
        self._evidence[item.scenario_id] = item

    def result(self, scenario_id: str) -> CertificationEvidence | None:
        return self._evidence.get(scenario_id)

    @property
    def evidence(self) -> tuple[CertificationEvidence, ...]:
        return tuple(self._evidence[name] for name in sorted(self._evidence))

    def implementation_complete(self) -> bool:
        required = {scenario.scenario_id for scenario in REQUIRED_CERTIFICATION_SCENARIOS}
        return required == set(self._evidence) and all(
            item.executed and item.actual_result is CertificationResult.PASS
            for item in self._evidence.values()
        )

    def limited_auto_ready(self, *, first_real_post_certified: bool = False) -> bool:
        return self.implementation_complete()

    def limited_auto_active(self, *, first_real_post_certified: bool, operator_authorized: bool = False) -> bool:
        return bool(self.implementation_complete() and first_real_post_certified and operator_authorized)
