from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.database.repository import Repository
from alpha_market_engine.market.brain import MarketBrainService
from alpha_market_engine.market.providers import AlpacaMarketDataProvider, RobinhoodMarketDataProvider


SYMBOLS = ("SPY", "QQQ", "DIA", "IWM")


def _fmt_value(value) -> str:
    if value is None:
        return "n/a"
    return str(value)


def _print_section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _certification_payload(result, *, persistence: dict[str, str]) -> dict[str, Any]:
    regime_support = result.regime.supporting_data
    missing = regime_support.get("missing_evidence", {})
    regime_payload = {
        "regime": result.regime.regime,
        "confidence": result.regime.confidence,
        "scores": result.regime.scores,
        "evidence": result.regime.evidence,
        "warnings": result.regime.warnings,
        "all_regime_scores": result.regime.scores,
        "winning_regime": result.regime.regime,
        "winning_score": regime_support.get("winning_score", result.regime.scores.get(result.regime.regime.value)),
        "second_highest_regime": regime_support.get("second_highest_regime"),
        "second_highest_score": regime_support.get("second_highest_score"),
        "score_gap": regime_support.get("score_gap"),
        "raw_regime_confidence": regime_support.get("raw_confidence", result.regime.confidence),
        "analysis_data_confidence": regime_support.get("analysis_data_confidence", regime_support.get("cross_validation_confidence")),
        "final_regime_confidence": result.regime.confidence,
        "missing_structure_count": missing.get("structure", 0),
        "missing_momentum_count": missing.get("momentum", 0),
        "missing_vwap_count": missing.get("vwap", 0),
        "confidence_penalties": regime_support.get("confidence_penalties", {}),
        "data_confidence_cap_applied": regime_support.get("data_confidence_cap_applied", False),
    }
    return {
        "analysis": {
            "analysis_id": result.analysis_id,
            "analysis_started_at": result.analysis_started_at,
            "source_captured_at": result.source_captured_at,
            "analysis_session_date": result.analysis_session_date,
            "analysis_confidence": result.analysis_confidence,
            "persistence": persistence,
        },
        "normalized_source": result.analysis_input,
        "cross_validation": result.cross_validation,
        "price_action": result.price_action,
        "structures": result.structures,
        "momentum": result.momentum,
        "divergences": result.divergences,
        "regime": regime_payload,
        "observations": result.observations,
    }


async def main() -> int:
    load_dotenv(override=False)

    primary = RobinhoodMarketDataProvider()
    secondary = None
    api_key = os.getenv("ALPACA_API_KEY") or ""
    api_secret = os.getenv("ALPACA_API_SECRET") or ""
    if api_key and api_secret:
        secondary = AlpacaMarketDataProvider(
            api_key=api_key,
            api_secret=api_secret,
            base_url=os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets"),
            data_feed=os.getenv("ALPACA_DATA_FEED", "iex"),
        )

    repository = None
    database_session = None
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        engine = create_engine(database_url, pool_pre_ping=True, future=True)
        session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
        database_session = session_factory()
        repository = Repository(database_session)

    try:
        service = MarketBrainService(primary, secondary=secondary, symbols=list(SYMBOLS), repository=repository)
        result = await service.analyze_market()
        persistence = (
            {"status": "ENABLED", "reason": "DEVELOPMENT_DATABASE_REPOSITORY_CONFIGURED"}
            if repository is not None
            else {"status": "DISABLED", "reason": "DATABASE_URL_UNAVAILABLE"}
        )
        if "--certify" in sys.argv or "--debug-facts" in sys.argv:
            import json

            print(json.dumps(_json_safe(_certification_payload(result, persistence=persistence)), indent=2, sort_keys=True))
            return 0

        print("ALPHA TRADING FLOOR — MARKET BRAIN")
        print()
        print(f"TIME: {result.snapshot.snapshot_time.astimezone(UTC).isoformat()}")
        print(f"SESSION: {result.snapshot.market_session.value}")
        print(f"DATA CONFIDENCE: {result.analysis_confidence.score}")
        print(f"PERSISTENCE: {persistence['status']}")
        print(f"PERSISTENCE REASON: {persistence['reason']}")

        _print_section("MARKET REGIME")
        print(f"{result.regime.regime.value}")
        print(f"Confidence: {result.regime.confidence}")
        for warning in result.regime.warnings:
            print(f"Warning: {warning}")

        _print_section("INDEX STATUS")
        normalized = {item.symbol: item for item in result.analysis_input.symbols}
        for symbol in SYMBOLS:
            source = normalized.get(symbol)
            if source is None:
                print(f"{symbol}: unavailable")
                continue
            structure = result.structures[symbol].structure.value if symbol in result.structures else "n/a"
            momentum = result.momentum[symbol].regime.value if symbol in result.momentum else "n/a"
            vwap = _fmt_value(source.session_vwap)
            print(
                f"{symbol} | Change: {_fmt_value(source.change_percent)} | VWAP: {vwap} | "
                f"Structure: {structure} | Momentum: {momentum}"
            )

        _print_section("CROSS VALIDATION")
        for key, item in sorted(result.cross_validation.items()):
            print(
                f"{key}: {item.status.value} | primary={item.primary_value} secondary={item.secondary_value} "
                f"diff={item.percentage_difference if item.percentage_difference is not None else 'n/a'}"
            )

        _print_section("PRICE ACTION EVENTS")
        any_events = False
        for symbol, analysis in result.price_action.items():
            for event in analysis.events:
                any_events = True
                print(f"* {symbol} {event.event_type.value} [{event.lifecycle.value}]")
        if not any_events:
            print("* none")

        _print_section("OBSERVATIONS")
        if not result.observations:
            print("[none]")
        else:
            for observation in result.observations:
                print(f"[{observation.confidence_score}] {observation.observation_type.value}")
                print("Facts:")
                for fact in observation.facts:
                    print(f"* {fact}")
                print(f"Boundary: {observation.interpretation_boundary}")
                print()

        _print_section("FACTUAL SAMPLE VERIFICATION")
        sample_checks = [
            f"{source.symbol} change={source.change_percent} vwap={_fmt_value(source.session_vwap)}"
            for source in result.analysis_input.symbols
        ]
        for line in sample_checks[:5]:
            print(f"* {line}")

        return 0
    finally:
        if database_session is not None:
            database_session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
