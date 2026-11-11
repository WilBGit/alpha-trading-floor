from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.database.repository import Repository
from alpha_market_engine.market.providers import AlpacaMarketDataProvider
from alpha_market_engine.market.snapshot import DataQualityStatus, MarketSnapshotService


def _market_snapshot_summary(snapshot) -> str:
    lines = [
        "ALPHA MARKET SNAPSHOT",
        f"Time: {snapshot.snapshot_time.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"Session: {snapshot.market_session.value}",
        f"Provider: {snapshot.provider}",
        f"Feed: {snapshot.data_feed or 'unknown'}",
        f"Quality: {snapshot.data_quality_status.value}",
        f"Oldest data timestamp: {snapshot.oldest_data_timestamp.isoformat() if snapshot.oldest_data_timestamp else 'unknown'}",
        f"Newest data timestamp: {snapshot.newest_data_timestamp.isoformat() if snapshot.newest_data_timestamp else 'unknown'}",
        f"Data age seconds: {snapshot.data_age_seconds if snapshot.data_age_seconds is not None else 'unknown'}",
        "",
    ]
    for symbol in ("SPY", "QQQ", "DIA", "IWM"):
        quote = getattr(snapshot, symbol.lower())
        if quote is None:
            lines.append(f"{symbol:<4} UNAVAILABLE")
            continue
        delayed = "YES" if quote.is_delayed else "NO"
        vwap_state = "UNAVAILABLE"
        if quote.above_vwap is True:
            vwap_state = "ABOVE VWAP"
        elif quote.above_vwap is False:
            vwap_state = "BELOW VWAP"
        lines.append(
            f"{symbol:<4} price={quote.price} prev_close={quote.previous_close if quote.previous_close is not None else 'n/a'} "
            f"change_pct={quote.change_percent if quote.change_percent is not None else 'n/a'} "
            f"quote_ts={quote.quote_timestamp.isoformat()} "
            f"age_sec={max(0, int((snapshot.snapshot_time - quote.quote_timestamp).total_seconds()))} "
            f"delayed={delayed} vwap={vwap_state}"
        )
    lines.append("")
    breadth = snapshot.market_breadth
    if breadth is None:
        lines.append("Breadth status: UNAVAILABLE")
    else:
        lines.append(f"Breadth status: adv={breadth.advancers} dec={breadth.decliners} up_vol={breadth.up_volume} down_vol={breadth.down_volume}")
    if snapshot.vix is None:
        lines.append("VIX status: UNAVAILABLE")
    else:
        lines.append(f"VIX status: price={snapshot.vix.price} change_pct={snapshot.vix.change_percent}")
    if snapshot.sector_performance:
        lines.append(f"Sector status: {len(snapshot.sector_performance)} sectors")
    else:
        lines.append("Sector status: UNAVAILABLE")
    lines.append("")
    if snapshot.missing_fields:
        lines.append("Missing fields:")
        for field in snapshot.missing_fields:
            lines.append(f"* {field}")
    else:
        lines.append("Missing fields: none")
    lines.append(f"Snapshot stored: {'YES' if snapshot.raw_data.get('stored') else 'NO'}")
    return "\n".join(lines)


async def main() -> int:
    load_dotenv(override=False)
    provider_name = os.getenv("MARKET_DATA_PROVIDER", "alpaca").lower()
    if provider_name != "alpaca":
        raise SystemExit(f"Unsupported MARKET_DATA_PROVIDER: {provider_name}")

    api_key = os.getenv("ALPACA_API_KEY") or ""
    api_secret = os.getenv("ALPACA_API_SECRET") or ""
    if not api_key or not api_secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_API_SECRET are required to run a live market snapshot")

    provider = AlpacaMarketDataProvider(
        api_key=api_key,
        api_secret=api_secret,
        base_url=os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets"),
        data_feed=os.getenv("ALPACA_DATA_FEED", "iex"),
    )

    repository = None
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        engine = create_engine(database_url, pool_pre_ping=True, future=True)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
        repository = Repository(SessionLocal())

    try:
        service = MarketSnapshotService(provider, repository=repository)
        snapshot = await service.create_snapshot()
        print(_market_snapshot_summary(snapshot))
        return 0
    finally:
        await provider.close()
        if repository is not None:
            repository.session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
