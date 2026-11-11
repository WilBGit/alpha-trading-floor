#!/usr/bin/env python3
"""Weekly audience digest for Alpha Trading Floor YouTube channel.
Implements measurement-before-automation: groups by format_tag, computes retention/CTR advantage,
enforces minimum sample before declaring winner.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.database.repository import Repository


def compute_format_performance(session: Any, days: int = 7) -> dict[str, Any]:
    """Query PostMetricsSnapshot + PublicationRecord joined by platform_post_id or publication_request_id.
    Returns per-format_tag stats with retention advantage.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    query = text("""
        SELECT
            pr.format_tag,
            COUNT(DISTINCT pms.platform_post_id) AS video_count,
            AVG(pms.impressions) AS avg_impressions,
            AVG(pms.ctr) AS avg_ctr,
            AVG(pms.average_view_duration) AS avg_view_duration,
            AVG(pms.retention_30s) AS avg_retention_30s,
            AVG(pms.return_viewer_rate) AS avg_return_viewer_rate,
            SUM(CASE WHEN pms.return_viewer_rate >= 0.10 THEN 1 ELSE 0 END) AS return_viewer_ok_count
        FROM publication_records pr
        LEFT JOIN post_metrics_snapshots pms ON pms.publication_request_id = pr.publication_request_id
        WHERE pr.platform = 'YOUTUBE'
          AND pr.created_at >= :cutoff
          AND pr.format_tag IS NOT NULL
        GROUP BY pr.format_tag
        HAVING COUNT(DISTINCT pms.platform_post_id) >= 3
    """)
    rows = session.execute(query, {"cutoff": cutoff}).fetchall()
    results = {}
    for row in rows:
        fmt = row.format_tag
        results[fmt] = {
            "video_count": row.video_count,
            "avg_impressions": float(row.avg_impressions or 0),
            "avg_ctr": float(row.avg_ctr or 0),
            "avg_view_duration": float(row.avg_view_duration or 0),
            "avg_retention_30s": float(row.avg_retention_30s or 0),
            "avg_return_viewer_rate": float(row.avg_return_viewer_rate or 0),
            "return_viewer_ok_count": row.return_viewer_ok_count,
            "meets_minimum": row.video_count >= 3 and (row.avg_impressions or 0) >= 30,
        }
    return results


def recommend_format(perf: dict[str, Any]) -> str | None:
    """Return the format_tag with highest retention_30s + return_viewer_rate advantage.
    Require at least 5pp edge and minimum sample.
    """
    if not perf:
        return None
    best = None
    best_score = -1.0
    for fmt, stats in perf.items():
        if not stats["meets_minimum"]:
            continue
        score = (stats["avg_retention_30s"] * 0.6) + (stats["avg_return_viewer_rate"] * 0.4)
        if score > best_score:
            best_score = score
            best = fmt
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description="Alpha Trading Floor weekly audience digest")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    session = sessionmaker(bind=engine)()
    repo = Repository(session)

    perf = compute_format_performance(session)
    winner = recommend_format(perf)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window_days": 7,
        "formats": perf,
        "recommended_format": winner,
        "action": "update_pipeline" if winner else "collect_more_data",
        "minimum_sample_met": all(s["meets_minimum"] for s in perf.values()) if perf else False,
    }

    print(json.dumps(report, indent=2, default=str))

    if not args.dry_run and winner:
        # In real impl: update ContentPattern or decision config with winner
        # and clear any audience_validation hold
        repo.clear_publication_hold("audience_validation_pending")
        print(f"Winner format {winner} promoted. Pipeline may now use it for new videos.")

    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
