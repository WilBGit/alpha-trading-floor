from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .brain_types import AnalysisConfidence, ConfidencePenalty, CrossValidationStatus, clamp_score


@dataclass(slots=True)
class ConfidenceRules:
    missing_core_field_penalty: int = 10
    divergent_cross_validation_penalty: int = 5
    secondary_unavailable_penalty: int = 3
    coverage_penalty_per_missing_percent: int = 1
    degraded_vwap_penalty: int = 4
    invalid_vwap_penalty: int = 10


class MarketConfidenceCalculator:
    """Deterministic confidence model.

    Penalty rules:
    - missing core field: 10 points per missing core field
    - divergent cross-validation result: 5 points per field
    - secondary unavailable: 3 points per unavailable comparison
    - incomplete bar coverage: 1 point per missing percent below 100
    - degraded VWAP quality: 4 points
    - invalid VWAP quality: 10 points
    """

    def __init__(self, rules: ConfidenceRules | None = None) -> None:
        self.rules = rules or ConfidenceRules()

    def calculate(
        self,
        *,
        core_field_status: dict[str, bool],
        cross_validation_statuses: list[CrossValidationStatus],
        session_bar_coverage: dict[str, Decimal],
        vwap_quality: dict[str, str],
        data_sources: list[str] | None = None,
    ) -> AnalysisConfidence:
        penalties: list[ConfidencePenalty] = []
        warnings: list[str] = []
        score = 100

        for symbol, available in core_field_status.items():
            if not available:
                penalties.append(ConfidencePenalty(reason=f"missing core field for {symbol}", penalty=self.rules.missing_core_field_penalty))
                score -= self.rules.missing_core_field_penalty
                warnings.append(f"Missing core data for {symbol}")

        for status in cross_validation_statuses:
            if status == CrossValidationStatus.DIVERGENT:
                penalties.append(ConfidencePenalty(reason="provider divergence", penalty=self.rules.divergent_cross_validation_penalty))
                score -= self.rules.divergent_cross_validation_penalty
                warnings.append("Cross-validation divergence detected")
            elif status == CrossValidationStatus.SECONDARY_UNAVAILABLE:
                warnings.append("Secondary provider unavailable; primary data retained")

        for symbol, coverage in session_bar_coverage.items():
            missing_percent = max(Decimal("0"), Decimal("100") - Decimal(str(coverage)))
            if missing_percent > 0:
                penalty = int(missing_percent) * self.rules.coverage_penalty_per_missing_percent
                if penalty > 0:
                    penalties.append(ConfidencePenalty(reason=f"incomplete bar coverage for {symbol}", penalty=penalty))
                    score -= penalty
                    warnings.append(f"Incomplete bar coverage for {symbol}: {coverage}%")

        for symbol, quality in vwap_quality.items():
            normalized = quality.upper()
            if normalized == "DEGRADED":
                penalties.append(ConfidencePenalty(reason=f"degraded VWAP quality for {symbol}", penalty=self.rules.degraded_vwap_penalty))
                score -= self.rules.degraded_vwap_penalty
                warnings.append(f"VWAP degraded for {symbol}")
            elif normalized == "INVALID":
                penalties.append(ConfidencePenalty(reason=f"invalid VWAP quality for {symbol}", penalty=self.rules.invalid_vwap_penalty))
                score -= self.rules.invalid_vwap_penalty
                warnings.append(f"VWAP invalid for {symbol}")

        return AnalysisConfidence(
            score=clamp_score(score),
            penalties=penalties,
            warnings=warnings,
            data_sources=data_sources or [],
        )
