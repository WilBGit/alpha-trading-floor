from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.auth.x_oauth import XOAuthService
from alpha_market_engine.content.drafting import CodexDraftingProvider, ContentDraftingService, FakeDraftingProvider
from alpha_market_engine.content.observability import PublicationReconciliationService, XMetricsProvider, XReconciliationProvider
from alpha_market_engine.content.opportunities import ContentOpportunityEngine
from alpha_market_engine.content.publication import ContentPublicationService, XApiPublishingProvider
from alpha_market_engine.database.repository import Repository
from alpha_market_engine.market.brain import MarketBrainService
from alpha_market_engine.market.providers import AlpacaMarketDataProvider, RobinhoodMarketDataProvider
from alpha_market_engine.production import AlphaTradingFloorOrchestrator, LimitedAutoPolicy, PublicationJobWorker, ProductionSafetyService


@dataclass(slots=True)
class ProductionRuntime:
    engine: object
    session_factory: object
    session: object
    repository: Repository
    oauth: XOAuthService
    market_brain: MarketBrainService
    opportunity_engine: ContentOpportunityEngine
    drafting_service: ContentDraftingService
    publication_service: ContentPublicationService
    reconciliation_service: PublicationReconciliationService
    metrics_provider: XMetricsProvider
    job_worker: PublicationJobWorker
    safety: ProductionSafetyService
    orchestrator: AlphaTradingFloorOrchestrator

    def close(self) -> None:
        self.session.close()
        self.engine.dispose()


def build_runtime(*, certification: bool = False, fake_drafting: bool = False, overrides: dict | None = None) -> ProductionRuntime:
    overrides = overrides or {}
    database_url = overrides.get("database_url") or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for production runtime")
    engine = create_engine(database_url, pool_pre_ping=True, future=True)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    repository = overrides.get("repository") or Repository(session)
    oauth = overrides.get("oauth") or XOAuthService(expected_username=os.getenv("X_EXPECTED_USERNAME", ""))
    primary = overrides.get("primary_provider") or RobinhoodMarketDataProvider()
    secondary = overrides.get("secondary_provider")
    if secondary is None and os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_API_SECRET"):
        secondary = AlpacaMarketDataProvider(api_key=os.environ["ALPACA_API_KEY"], api_secret=os.environ["ALPACA_API_SECRET"], base_url=os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets"), data_feed=os.getenv("ALPACA_DATA_FEED", "iex"))
    market_brain = overrides.get("market_brain") or MarketBrainService(primary, secondary=secondary, symbols=["SPY", "QQQ", "DIA", "IWM"], repository=repository)
    opportunity_engine = overrides.get("opportunity_engine") or ContentOpportunityEngine(repository=repository, max_post_candidates_per_cycle=1)
    if overrides.get("drafting_provider") is not None:
        drafting_provider = overrides["drafting_provider"]
    elif fake_drafting or certification:
        drafting_provider = FakeDraftingProvider()
    else:
        drafting_provider = CodexDraftingProvider()
    safety = overrides.get("safety") or ProductionSafetyService(repository)
    drafting_service = overrides.get("drafting_service") or ContentDraftingService(drafting_provider, repository=repository, safety=safety)
    publishing_provider = overrides.get("publishing_provider") or XApiPublishingProvider(token_provider=oauth, expected_username=os.getenv("X_EXPECTED_USERNAME", ""))
    publication_service = overrides.get("publication_service") or ContentPublicationService(publishing_provider, repository=repository, auto_post_enabled=False if certification else os.getenv("AUTO_POST_ENABLED", "false").lower() == "true", safety=safety)
    reconciliation_provider = overrides.get("reconciliation_provider") or XReconciliationProvider(token_provider=oauth, expected_username=os.getenv("X_EXPECTED_USERNAME", ""))
    reconciliation_service = overrides.get("reconciliation_service") or PublicationReconciliationService(reconciliation_provider, repository=repository, safety=safety)
    metrics_provider = overrides.get("metrics_provider") or XMetricsProvider(token_provider=oauth)
    worker = overrides.get("job_worker") or PublicationJobWorker(repository, {}, reconciliation_service=reconciliation_service, publication_service=publication_service, metrics_provider=metrics_provider, oauth=oauth, certification=certification, safety=safety)
    orchestrator = overrides.get("orchestrator") or AlphaTradingFloorOrchestrator(market_brain=market_brain, opportunity_engine=opportunity_engine, drafting_service=drafting_service, publication_service=publication_service, repository=repository, policy=LimitedAutoPolicy.from_env(), runner_id=f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}")
    return ProductionRuntime(engine, session_factory, session, repository, oauth, market_brain, opportunity_engine, drafting_service, publication_service, reconciliation_service, metrics_provider, worker, safety, orchestrator)
