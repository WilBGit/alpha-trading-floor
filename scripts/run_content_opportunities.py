from __future__ import annotations

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

from alpha_market_engine.content.opportunities import ContentOpportunityEngine
from alpha_market_engine.database.repository import Repository
from alpha_market_engine.market.brain import MarketBrainService
from alpha_market_engine.market.providers import AlpacaMarketDataProvider, RobinhoodMarketDataProvider

SYMBOLS = ["SPY", "QQQ", "DIA", "IWM"]


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, (Decimal, datetime)):
        return str(value.astimezone(UTC).isoformat() if isinstance(value, datetime) else value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


async def main() -> int:
    load_dotenv(override=False)
    primary = RobinhoodMarketDataProvider()
    secondary = None
    if os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_API_SECRET"):
        secondary = AlpacaMarketDataProvider(
            api_key=os.environ["ALPACA_API_KEY"],
            api_secret=os.environ["ALPACA_API_SECRET"],
            base_url=os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets"),
            data_feed=os.getenv("ALPACA_DATA_FEED", "iex"),
        )
    database_session = None
    repository = None
    if os.getenv("DATABASE_URL"):
        engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True, future=True)
        database_session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
        repository = Repository(database_session)
    try:
        market_result = await MarketBrainService(primary, secondary=secondary, symbols=SYMBOLS, repository=repository).analyze_market()
        opportunity_result = ContentOpportunityEngine(repository=repository).evaluate(market_result)
        print(json.dumps(json_safe(opportunity_result), indent=2, sort_keys=True))
        return 0
    finally:
        if database_session is not None:
            database_session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
