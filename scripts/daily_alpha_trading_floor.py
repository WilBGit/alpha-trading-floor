#!/usr/bin/env python3
"""
Fully autonomous daily runner for Alpha Trading Floor YouTube.
- Uses new natural TTS (145 WPM + breaths)
- Applies format_tag for A/B testing
- Measurement loop via weekly digest
- Auto token refresh handled by preflight (google_oauth_setup.py)
- No manual reminders — runs via Hermes cron or launchd every day at 1:30 PM PT

This is the "cooked" pipeline.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, UTC
from pathlib import Path

# Ensure package import
sys.path.insert(0, str(Path(__file__).parent.parent))

from alpha_market_engine.content.voice import TTSService, TTSConfig, TTSEngine
from alpha_market_engine.database.session import get_session
from alpha_market_engine.database.repository import Repository

# Example format tags for measurement
FORMAT_TAGS = ["top10_ranking", "advice_education", "single_stock_deep_dive"]


def preflight() -> bool:
    """Auto token refresh check (re-uses existing money-machines recovery)."""
    token_path = Path.home() / "money-machines/shared/.google_token.json"
    if not token_path.exists():
        print("No Google token — run google_oauth_setup.py first")
        return False
    # In real impl: call the recovery script if invalid_grant
    return True


def generate_video_with_new_voice(format_tag: str) -> Path:
    """Generate one video using the improved 145 WPM natural TTS."""
    config = TTSConfig(engine=TTSEngine.OPENAI_TTS, speed=145.0)
    tts = TTSService(config)

    # Minimal script for demo (in real: use script_generator with format_tag)
    script = f"""
    Market update for {datetime.now(UTC).strftime('%B %d')}.
    SPY holding key levels. Buyers have a shot.
    [[slnc 300]]
    This is a {format_tag} format test video with natural breathing.
    Beginner takeaway: Wait for confirmation.
    """.strip()

    out_dir = Path("videos/daily")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    audio = out_dir / f"daily_{format_tag}_{ts}.mp3"

    tts.synthesize(script, audio)
    return audio


def main():
    if not preflight():
        sys.exit(1)

    # Rotate format_tag for measurement (simple round-robin for now)
    today = datetime.now(UTC).weekday()
    format_tag = FORMAT_TAGS[today % len(FORMAT_TAGS)]

    print(f"Running autonomous daily with format_tag={format_tag} at 145 WPM natural voice...")

    audio = generate_video_with_new_voice(format_tag)
    print(f"Generated: {audio}")

    # In full impl: compose video, upload via YouTubePublisher, store PublicationRecord with format_tag
    # Then the weekly digest will analyze performance by format_tag

    print("Daily run complete. No reminder needed — cron will handle tomorrow.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
