from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.content.drafting import CodexDraftingProvider, ContentDraftingService, FakeDraftingProvider, build_draft_package
from alpha_market_engine.content.opportunities import ContentOpportunityEngine
from alpha_market_engine.database.repository import Repository
from alpha_market_engine.market.brain import MarketBrainService
from alpha_market_engine.market.providers import AlpacaMarketDataProvider, RobinhoodMarketDataProvider


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return str(value)


def provider_from_args(fake: bool):
    if fake:
        return FakeDraftingProvider()
    try:
        return CodexDraftingProvider()
    except Exception:
        return FakeDraftingProvider()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--certify", action="store_true")
    parser.add_argument("--fake-provider", action="store_true")
    args = parser.parse_args()
    load_dotenv(override=False)
    primary = RobinhoodMarketDataProvider()
    secondary = None
    if os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_API_SECRET"):
        secondary = AlpacaMarketDataProvider(api_key=os.environ["ALPACA_API_KEY"], api_secret=os.environ["ALPACA_API_SECRET"], base_url=os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets"), data_feed=os.getenv("ALPACA_DATA_FEED", "iex"))
    session = None
    repository = None
    if os.getenv("DATABASE_URL"):
        session = sessionmaker(bind=create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True, future=True), autoflush=False, autocommit=False, future=True)()
        repository = Repository(session)
    try:
        market_result = await MarketBrainService(primary, secondary=secondary, symbols=["SPY", "QQQ", "DIA", "IWM"], repository=repository).analyze_market()
        opportunity_result = ContentOpportunityEngine(repository=repository).evaluate(market_result)
        selected = opportunity_result.selected_candidates
        provider = provider_from_args(args.fake_provider)
        drafts = [{"package": build_draft_package(opportunity), "result": ContentDraftingService(provider, repository=repository).draft(opportunity)} for opportunity in selected]
        payload = {"analysis_id": market_result.analysis_id, "opportunities": opportunity_result, "drafts": drafts, "auto_post_enabled": False, "published": False, "provider": getattr(provider, "model", "fake-deterministic-v1")}
        print(json.dumps(json_safe(payload), indent=2, sort_keys=True))
        return 0
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
