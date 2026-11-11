from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from alpha_market_engine.market.regime import MarketRegimeDecision
from alpha_market_engine.market.snapshot import NormalizedMarketSnapshot


@dataclass(slots=True)
class ContentOpportunity:
    opportunity_type: str
    symbols: list[str]
    title: str
    observation: str
    importance_score: int
    time_sensitivity_score: int
    status: str = "DETECTED"
    detected_at: datetime | None = None
    supporting_data: dict | None = None


class ContentOpportunityDetector:
    def detect(self, snapshot: NormalizedMarketSnapshot, regime: MarketRegimeDecision, catalysts: list[dict] | None = None) -> list[ContentOpportunity]:
        catalysts = catalysts or []
        opportunities: list[ContentOpportunity] = []
        if regime.regime in {"ROTATION", "RISK_OFF", "RISK_ON", "HIGH_VOLATILITY"}:
            opportunities.append(
                ContentOpportunity(
                    opportunity_type="MARKET_CHECK",
                    symbols=["SPY", "QQQ"],
                    title=f"{regime.regime.replace('_', ' ').title()} market read",
                    observation=regime.reasoning,
                    importance_score=85 if regime.regime != "RISK_ON" else 70,
                    time_sensitivity_score=80 if regime.regime == "HIGH_VOLATILITY" else 65,
                    supporting_data={"regime": regime.regime, "supporting_data": regime.supporting_data},
                )
            )
        if catalysts:
            top = catalysts[0]
            opportunities.append(
                ContentOpportunity(
                    opportunity_type="NEWS_CATALYST",
                    symbols=list(top.get("symbols") or top.get("symbols_json") or [])[:3],
                    title=str(top.get("headline", "Catalyst")),
                    observation="Fresh catalyst worth watching.",
                    importance_score=int(top.get("impact_score", 65)),
                    time_sensitivity_score=90,
                    supporting_data=top,
                )
            )
        return opportunities
