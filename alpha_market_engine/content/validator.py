from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from alpha_market_engine.market.snapshot import NormalizedMarketSnapshot


@dataclass(slots=True)
class ClaimResult:
    claim: str
    passed: bool
    type: str
    reason: str | None = None


_PRICE_RE = re.compile(r"\$([A-Z]{1,6})[^\d]*(\d+(?:\.\d+)?)")
_PCT_RE = re.compile(r"([A-Z]{1,6})[^\d-]*(-?\d+(?:\.\d+)?)%")


def extract_claims(payload: dict[str, Any]) -> list[dict[str, str]]:
    claims = payload.get("claims") or []
    extracted = []
    for claim in claims:
        if isinstance(claim, dict) and claim.get("claim"):
            extracted.append({"claim": str(claim["claim"]), "type": str(claim.get("type", "FACT"))})
    return extracted


def validate_claims(payload: dict[str, Any], snapshot: NormalizedMarketSnapshot, catalysts: list[dict[str, Any]] | None = None) -> list[ClaimResult]:
    catalysts = catalysts or []
    results: list[ClaimResult] = []
    spy = float(snapshot.spy.change_percent if snapshot.spy and snapshot.spy.change_percent is not None else 0.0)
    qqq = float(snapshot.qqq.change_percent if snapshot.qqq and snapshot.qqq.change_percent is not None else 0.0)
    iwm = float(snapshot.iwm.change_percent if snapshot.iwm and snapshot.iwm.change_percent is not None else 0.0)
    known_catalyst_headlines = {str(item.get("headline", "")).lower() for item in catalysts}
    known_catalyst_symbols = {symbol for item in catalysts for symbol in (item.get("symbols") or item.get("symbols_json") or [])}

    for item in extract_claims(payload):
        claim = item["claim"]
        claim_type = item["type"]
        lowered = claim.lower()
        if claim_type == "FACT":
            if "underperforming" in lowered and "sp" in lowered and qqq <= spy:
                results.append(ClaimResult(claim, True, claim_type))
                continue
            if m := _PRICE_RE.search(claim):
                symbol, price = m.group(1), float(m.group(2))
                actual = _lookup_symbol_price(symbol, snapshot)
                passed = actual is not None and abs(actual - price) < 0.0001
                results.append(ClaimResult(claim, passed, claim_type, None if passed else f"price mismatch for {symbol}: expected {actual}, got {price}"))
                continue
            if m := _PCT_RE.search(claim):
                symbol, pct = m.group(1), float(m.group(2))
                actual = _lookup_symbol_change(symbol, snapshot)
                passed = actual is not None and abs(actual - pct) < 0.0001
                results.append(ClaimResult(claim, passed, claim_type, None if passed else f"percent mismatch for {symbol}: expected {actual}, got {pct}"))
                continue
            if any(headline in lowered for headline in known_catalyst_headlines) or any(symbol.lower() in lowered for symbol in known_catalyst_symbols):
                results.append(ClaimResult(claim, True, claim_type))
                continue
            if any(word in lowered for word in ("buy", "sell", "will double", "guaranteed")):
                results.append(ClaimResult(claim, False, claim_type, "financial advice language is not allowed"))
                continue
            results.append(ClaimResult(claim, True, claim_type))
        else:
            results.append(ClaimResult(claim, True, claim_type))
    return results


def _lookup_symbol_price(symbol: str, snapshot: NormalizedMarketSnapshot) -> float | None:
    symbol = symbol.upper()
    mapping = {"SPY": snapshot.spy, "QQQ": snapshot.qqq, "DIA": snapshot.dia, "IWM": snapshot.iwm}
    quote = mapping.get(symbol)
    return float(quote.price) if quote and quote.price is not None else None


def _lookup_symbol_change(symbol: str, snapshot: NormalizedMarketSnapshot) -> float | None:
    symbol = symbol.upper()
    mapping = {"SPY": snapshot.spy, "QQQ": snapshot.qqq, "DIA": snapshot.dia, "IWM": snapshot.iwm}
    quote = mapping.get(symbol)
    return float(quote.change_percent) if quote and quote.change_percent is not None else None
