from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from alpha_market_engine.market.regime import MarketRegimeDecision
from alpha_market_engine.market.snapshot import NormalizedMarketSnapshot


class GeneratedClaim(BaseModel):
    claim: str
    type: str = Field(default="FACT")


class GeneratedPostPayload(BaseModel):
    post: str
    post_type: str
    symbols: list[str] = Field(default_factory=list)
    claims: list[GeneratedClaim] = Field(default_factory=list)
    tone: str = "ALPHA_TRADER"


@dataclass(slots=True)
class XPostGenerator:
    def generate(self, snapshot: NormalizedMarketSnapshot, regime: MarketRegimeDecision, opportunity_type: str = "MARKET_CHECK") -> GeneratedPostPayload:
        spy = snapshot.spy
        qqq = snapshot.qqq
        if regime.regime == "ROTATION":
            post = (
                "$SPY is holding better than $QQQ right now.\n\n"
                "That’s rotation, not panic. Tech is under pressure while the broader tape is still standing.\n\n"
                "I’m watching $SPY support and whether buyers can keep this move intact into the close."
            )
            claims = [
                GeneratedClaim(claim="$QQQ is underperforming $SPY", type="FACT"),
                GeneratedClaim(claim="This looks more like rotation than liquidation", type="INTERPRETATION"),
            ]
        elif regime.regime == "HIGH_VOLATILITY":
            post = (
                "Volatility is up and the tape is getting less forgiving.\n\n"
                f"$SPY {spy.change_percent:.2f}% | $QQQ {qqq.change_percent:.2f}%\n\n"
                "Momentum is there. Confirmation isn’t."
            )
            claims = [
                GeneratedClaim(claim=f"$SPY is {spy.change_percent:.2f}%", type="FACT"),
                GeneratedClaim(claim=f"$QQQ is {qqq.change_percent:.2f}%", type="FACT"),
            ]
        else:
            post = (
                f"$SPY {spy.change_percent:.2f}% | $QQQ {qqq.change_percent:.2f}%\n\n"
                "Price is telling the story. I’m watching the levels, not the noise."
            )
            claims = [
                GeneratedClaim(claim=f"$SPY is {spy.change_percent:.2f}%", type="FACT"),
                GeneratedClaim(claim=f"$QQQ is {qqq.change_percent:.2f}%", type="FACT"),
            ]
        symbols = ["SPY", "QQQ"]
        return GeneratedPostPayload(post=post, post_type=opportunity_type, symbols=symbols, claims=claims, tone="ALPHA_TRADER")
