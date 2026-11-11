from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import StatementError

from alpha_market_engine.content.decision import PublishContext, can_publish
from alpha_market_engine.database.models import (
    CatalystCategory,
    ContentOpportunity,
    GeneratedPost,
    GeneratedPostStatus,
    MarketCatalyst,
    MarketRegime,
    MarketRegimeName,
    MarketSnapshot,
    OpportunityStatus,
    PublishedPost,
    SystemEvent,
    SystemEventSeverity,
    TickerSnapshot,
)
from alpha_market_engine.database.repository import Repository


def test_repository_crud_round_trip(db_session):
    repo = Repository(db_session)
    now = datetime.now(timezone.utc)

    market_snapshot = repo.add(
        MarketSnapshot(
            snapshot_time=now,
            spy_price=620.125000,
            spy_change_percent=0.312500,
            qqq_price=550.250000,
            qqq_change_percent=0.140000,
            dia_price=430.125000,
            dia_change_percent=0.090000,
            iwm_price=225.500000,
            iwm_change_percent=0.220000,
            vix_value=18.250000,
            vix_change_percent=0.500000,
            market_breadth=0.120000,
            advancers=1800,
            decliners=1400,
            up_volume=900000,
            down_volume=750000,
            raw_data_json={"source": "integration-test"},
        )
    )
    repo.commit()
    repo.refresh(market_snapshot)

    latest_market_snapshot = db_session.query(MarketSnapshot).order_by(MarketSnapshot.snapshot_time.desc()).first()
    assert latest_market_snapshot is not None
    assert float(latest_market_snapshot.spy_price) == 620.125

    ticker_older = repo.add(
        TickerSnapshot(
            symbol="SPY",
            snapshot_time=now - timedelta(minutes=5),
            price=619.5,
            change_percent=0.1,
            volume=1000,
            relative_volume=0.9,
            raw_data_json={"snapshot": "older"},
        )
    )
    ticker_latest = repo.add(
        TickerSnapshot(
            symbol="SPY",
            snapshot_time=now,
            price=620.25,
            change_percent=0.35,
            volume=2000,
            relative_volume=1.1,
            raw_data_json={"snapshot": "latest"},
        )
    )
    repo.commit()
    repo.refresh(ticker_older)
    repo.refresh(ticker_latest)

    latest_ticker = (
        db_session.query(TickerSnapshot)
        .filter(TickerSnapshot.symbol == "SPY")
        .order_by(TickerSnapshot.snapshot_time.desc())
        .first()
    )
    assert latest_ticker is not None
    assert float(latest_ticker.price) == 620.25

    regime = repo.add(
        MarketRegime(
            detected_at=now,
            regime=MarketRegimeName.RISK_ON,
            confidence=91,
            reasoning="breadth positive and tech strong",
            supporting_data_json={"source": "integration-test"},
        )
    )
    repo.commit()
    repo.refresh(regime)

    latest_regime = db_session.query(MarketRegime).order_by(MarketRegime.detected_at.desc()).first()
    assert latest_regime is not None
    assert latest_regime.regime == MarketRegimeName.RISK_ON

    catalyst = repo.add(
        MarketCatalyst(
            headline="Fed holds rates steady",
            source="Reuters",
            source_url="https://example.com/fed",
            published_at=now,
            symbols_json=["SPY", "QQQ"],
            category=CatalystCategory.FED,
            impact_score=84,
            verified=True,
            content_hash="catalyst-hash-1",
        )
    )
    opportunity = repo.add(
        ContentOpportunity(
            detected_at=now,
            opportunity_type="MARKET_CHECK",
            symbols_json=["SPY", "QQQ"],
            title="SPY rotation watch",
            observation="QQQ lagging while SPY holds up",
            supporting_data_json={"catalyst_id": 1},
            importance_score=80,
            time_sensitivity_score=75,
            status=OpportunityStatus.DETECTED,
        )
    )
    repo.commit()
    repo.refresh(catalyst)
    repo.refresh(opportunity)

    generated = repo.add(
        GeneratedPost(
            platform="X",
            post_type="MARKET_CHECK",
            content="$SPY is holding better than $QQQ right now.",
            content_hash="post-hash-1",
            normalized_hash="post-hash-1-normalized",
            symbols_json=["SPY", "QQQ"],
            market_regime=MarketRegimeName.RISK_ON,
            source_opportunity_id=opportunity.id,
            ai_provider="local",
            ai_model="echo",
            fact_confidence_score=92,
            quality_score=88,
            duplicate_score=2,
            safety_status="PASS",
            validation_json={"claims_valid": True},
            status=GeneratedPostStatus.DRAFT,
            generated_at=now,
        )
    )
    repo.commit()
    repo.refresh(generated)

    generated.status = GeneratedPostStatus.APPROVED
    repo.commit()
    repo.refresh(generated)
    assert generated.status == GeneratedPostStatus.APPROVED

    published = repo.add(
        PublishedPost(
            generated_post_id=generated.id,
            platform="X",
            platform_post_id="x-post-123",
            content=generated.content,
            published_at=now,
            impressions=1200,
            likes=55,
            replies=8,
            reposts=4,
            bookmarks=12,
            engagement_rate=0.065000,
            last_metrics_update=now,
        )
    )
    repo.commit()
    repo.refresh(published)

    event = repo.add(
        SystemEvent(
            event_type="PUBLISH_SUCCESS",
            severity=SystemEventSeverity.INFO,
            component="publisher",
            message="Published X post successfully",
            metadata_json={"post_id": published.platform_post_id},
        )
    )
    repo.commit()
    repo.refresh(event)

    current_published = db_session.query(PublishedPost).filter_by(platform_post_id="x-post-123").one()
    assert current_published.generated_post_id == generated.id
    assert float(current_published.engagement_rate) == 0.065

    current_event = db_session.query(SystemEvent).order_by(SystemEvent.created_at.desc()).first()
    assert current_event is not None
    assert current_event.severity == SystemEventSeverity.INFO

    with pytest.raises(StatementError):
        db_session.add(
            GeneratedPost(
                platform="X",
                post_type="MARKET_CHECK",
                content="invalid",
                content_hash="post-hash-invalid",
                normalized_hash="post-hash-invalid-normalized",
                symbols_json=["SPY"],
                market_regime=MarketRegimeName.RISK_ON,
                source_opportunity_id=opportunity.id,
                ai_provider="local",
                ai_model="echo",
                fact_confidence_score=92,
                quality_score=88,
                duplicate_score=2,
                safety_status="PASS",
                validation_json={"claims_valid": True},
                status="BOGUS_STATUS",
                generated_at=now,
            )
        )
        db_session.flush()
    db_session.rollback()

    publish_context = PublishContext(
        auto_post_enabled=True,
        fact_confidence_score=92,
        quality_score=88,
        duplicate_score=2,
        safety_status="PASS",
        daily_post_count=1,
        max_daily_x_posts=8,
        opportunity_status="APPROVED",
    )
    assert can_publish(publish_context).allowed is True
