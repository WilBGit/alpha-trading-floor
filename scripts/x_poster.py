"""
Alpha Trading Floor — Minimal X Poster
========================================
One script. Three steps:
  1. Fetch fresh market data (Robinhood MCP → market snapshot)
  2. Generate a concise, factual X post via LLM
  3. Publish to X via OAuth2

No certification matrix, no reconciliation service, no database required.

Usage:
  python x_poster.py post          # Generate & post (or --dry-run to preview)
  python x_poster.py oauth-status  # Show OAuth token status
  python x_poster.py refresh-token # Force OAuth token refresh

State file: ~/.hermes/state/alpha_trading_floor/poster_state.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx

# ── Paths ─────────────────────────────────────────────────────────────────────

PROJECT = Path(__file__).resolve().parent
STATE_DIR = Path.home() / ".hermes" / "state" / "alpha_trading_floor"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "poster_state.json"
TOKEN_FILE = STATE_DIR.parent.parent / "state" / "alpha_trading_floor_x_oauth.json"

# ── Load .env ─────────────────────────────────────────────────────────────────

def _load_env():
    env = PROJECT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

# ── OAuth ─────────────────────────────────────────────────────────────────────

X_CLIENT_ID = os.getenv("X_CLIENT_ID", "")
X_CLIENT_SECRET = os.getenv("X_CLIENT_SECRET", "")
X_EXPECTED_USERNAME = os.getenv("X_EXPECTED_USERNAME", "AlphaTradingF")


def _load_token() -> dict | None:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    return None


def _save_token(data: dict):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(TOKEN_FILE)
    os.chmod(TOKEN_FILE, 0o600)


def _refresh_token() -> str:
    """Refresh the OAuth token. Returns valid access token."""
    token = _load_token()
    if not token or not token.get("refresh_token"):
        print("ERROR: No refresh token available. Run OAuth flow first.", file=sys.stderr)
        sys.exit(1)

    resp = httpx.post(
        "https://api.x.com/2/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": X_CLIENT_ID,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"ERROR: Token refresh failed: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)

    new = resp.json()
    token.update({
        "access_token": new["access_token"],
        "refresh_token": new.get("refresh_token", token["refresh_token"]),
        "expires_at": (datetime.now(UTC) + timedelta(seconds=new.get("expires_in", 7200))).isoformat(),
        "granted_scopes": new.get("scope", " ".join(token.get("granted_scopes", []))).split(),
    })
    _save_token(token)
    print(f"Token refreshed. Expires: {token['expires_at']}")
    return token["access_token"]


def _get_access_token() -> str:
    token = _load_token()
    if not token:
        print("ERROR: No OAuth token. Run OAuth flow first.", file=sys.stderr)
        sys.exit(1)

    # Check expiry
    expires_at = token.get("expires_at")
    if expires_at:
        exp = datetime.fromisoformat(expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if datetime.now(UTC) >= exp - timedelta(minutes=5):
            return _refresh_token()

    return token["access_token"]


def _verify_identity(access_token: str) -> dict:
    """Verify X identity. Returns {id, username, name}."""
    resp = httpx.get(
        "https://api.x.com/2/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"ERROR: Identity verification failed: {resp.status_code}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()["data"]
    if X_EXPECTED_USERNAME and data.get("username", "").lower() != X_EXPECTED_USERNAME.lower():
        print(f"ERROR: Identity mismatch. Expected {X_EXPECTED_USERNAME}, got {data.get('username')}", file=sys.stderr)
        sys.exit(1)
    return data


# ── Market Data (Robinhood MCP via Hermes) ─────────────────────────────────────

def _fetch_market_snapshot() -> dict:
    """Fetch real-time market data for SPY, QQQ, IWM.

    Returns dict like:
      {"SPY": {"price": 750.23, "change_pct": 0.45}, ...}
    """
    import asyncio
    sys.path.insert(0, str(PROJECT))
    from alpha_market_engine.market.providers.robinhood import RobinhoodMarketDataProvider

    async def _fetch():
        provider = RobinhoodMarketDataProvider()
        symbols = ["SPY", "QQQ", "IWM"]
        quotes = {}
        for sym in symbols:
            try:
                q = await provider.get_quote(sym)
                quotes[sym] = {
                    "price": float(q.price) if q.price else 0,
                    "change_pct": float(q.change_percent) if q.change_percent else 0,
                }
            except Exception as e:
                print(f"WARN: Could not fetch {sym}: {e}", file=sys.stderr)
        return quotes

    quotes = asyncio.run(_fetch())
    if not quotes:
        print("ERROR: No market data available.", file=sys.stderr)
        sys.exit(1)
    return quotes


def _fetch_market_via_mcp() -> dict:
    """Fetch market data. Uses env data from Hermes cron if available, else live Robinhood."""
    env_data = os.getenv("ALPHA_MARKET_DATA")
    if env_data:
        return json.loads(env_data)
    return _fetch_market_snapshot()


# ── LLM Draft ─────────────────────────────────────────────────────────────────

def _draft_post(market: dict, recent_posts: list[str]) -> tuple[str, str]:
    """Generate an X post using the configured LLM.

    Returns (post_text, rationale).
    """
    ai_provider = os.getenv("AI_PROVIDER", "hermes")
    ai_model = os.getenv("AI_MODEL", "grok-4.6")
    hermes_provider = os.getenv("AI_HERMES_PROVIDER", "xai-oauth")
    hermes_bin = os.getenv(
        "HERMES_BIN",
        "/Users/wilsonnguyen/.hermes/hermes-agent/.hermes/bin/hermes",
    )
    if not os.path.exists(hermes_bin):
        hermes_bin = shutil.which("hermes") or hermes_bin

    now_et = datetime.now(timezone(timedelta(hours=-4)))
    time_str = now_et.strftime("%I:%M %p ET")

    market_desc = "\n".join(
        f"  {sym}: ${q['price']:.2f} ({q['change_pct']:+.2f}%)"
        for sym, q in market.items()
    )

    dup_guidance = ""
    if recent_posts:
        dup_guidance = f"\n\nRecent posts (DO NOT repeat these angles):\n" + "\n".join(f"  - {p}" for p in recent_posts[-5:])

    prompt = f"""Current time: {time_str}
Market snapshot:
{market_desc}

Write a factual market update tweet under 280 characters. Rules:
- State exact prices with a $ prefix and signed percentage moves (for example, -0.12% rather than "down 0.12%")
- No predictions, no advice, no "I think"
- No hashtags, no emojis, no $CASHTAGS
- Plain English, one or two sentences

Tweet:"""

    text = ""
    for attempt in range(2):
        if ai_provider in {"hermes", "xai-oauth", "openai-codex"}:
            provider = ai_provider if ai_provider in {"xai-oauth", "openai-codex"} else hermes_provider
            result = subprocess.run(
                [
                    hermes_bin,
                    "chat",
                    "-Q",
                    "--ignore-rules",
                    "-t",
                    "safe",
                    "-q",
                    "You write brief, factual market updates for X. Output ONLY the post text, nothing else. No quotes, no preamble.\n\n" + prompt,
                    "-m",
                    ai_model,
                    "--provider",
                    provider,
                    "--reasoning",
                    "none",
                    "--max-turns",
                    "1",
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode != 0:
                print(f"ERROR: Hermes LLM call failed: exit {result.returncode}", file=sys.stderr)
                sys.exit(1)
            text = (result.stdout or "").strip()
            if "\nsession_id:" in text:
                text = text.split("\nsession_id:", 1)[0].rstrip()
        else:
            print(f"ERROR: Unsupported AI_PROVIDER={ai_provider!r}; use Hermes Grok/GPT routing", file=sys.stderr)
            sys.exit(1)

        # Remove quotes if the LLM wrapped the post in them
        if text and text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if text and text.startswith("'") and text.endswith("'"):
            text = text[1:-1]

        if text and len(text) >= 20:
            break
        print(f"WARN: LLM returned empty/short text (attempt {attempt+1}/3), retrying...", file=sys.stderr)

    if not text or len(text) < 10:
        print("ERROR: LLM failed to generate usable post after 3 attempts", file=sys.stderr)
        sys.exit(1)

    return text, f"LLM draft at {time_str}"


# ── Proofreading / fact validation ────────────────────────────────────────────

def _validate_post_text(text: str, market: dict) -> tuple[bool, list[str]]:
    """Fail closed on malformed or stale numeric claims before publication."""
    errors: list[str] = []
    if re.search(r"\$0{2,}(?:\.0+)?\b|\b(?:topped|above|below|at|is)\s+0{2,}(?:\.0+)?\b", text, re.I):
        errors.append("malformed_zero_price")

    for symbol, quote in market.items():
        if not re.search(rf"\b{re.escape(symbol)}\b", text, re.I):
            continue
        price = float(quote.get("price", 0))
        change = float(quote.get("change_pct", 0))
        if price <= 0:
            errors.append(f"missing_source_price_{symbol}")
            continue
        price_forms = {f"{price:.2f}", f"${price:.2f}"}
        if not any(form in text for form in price_forms):
            errors.append(f"price_mismatch_{symbol}_expected_{price:.2f}")
        change_forms = {f"{change:+.2f}%", f"{change:.2f}%"}
        if "%" in text and not any(form in text for form in change_forms):
            errors.append(f"change_mismatch_{symbol}_expected_{change:+.2f}%")
    return not errors, errors


# ── Publish ────────────────────────────────────────────────────────────────────

def _publish(text: str, *, dry_run: bool = False) -> dict:
    """Publish a tweet. Returns {id, text} or {dry_run: true, text}."""
    if dry_run:
        print(f"[DRY RUN] Would post: {text}")
        return {"dry_run": True, "text": text, "char_count": len(text)}

    access_token = _get_access_token()
    identity = _verify_identity(access_token)

    resp = httpx.post(
        "https://api.x.com/2/tweets",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"text": text},
        timeout=20,
    )

    if resp.status_code not in (200, 201):
        print(f"ERROR: Post failed: {resp.status_code} {resp.text[:300]}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()["data"]
    return {"id": data["id"], "text": data["text"]}


# ── State ──────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"posts": [], "last_post_at": None, "daily_count": 0, "daily_date": None}


def _save_state(state: dict):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(STATE_FILE)
    os.chmod(STATE_FILE, 0o600)


def _check_limits(state: dict) -> tuple[bool, str]:
    """Check daily limit and spacing. Returns (ok, reason)."""
    MAX_DAILY = int(os.getenv("ALPHA_DAILY_HARD_MAX", "10"))
    MIN_SPACING = int(os.getenv("ALPHA_MIN_POST_SPACING_SECONDS", "720"))

    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")

    # Reset daily counter
    if state.get("daily_date") != today:
        state["daily_date"] = today
        state["daily_count"] = 0

    if state["daily_count"] >= MAX_DAILY:
        return False, f"Daily limit reached ({state['daily_count']}/{MAX_DAILY})"

    if state.get("last_post_at"):
        last = datetime.fromisoformat(state["last_post_at"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        elapsed = (now - last).total_seconds()
        if elapsed < MIN_SPACING:
            remaining = int(MIN_SPACING - elapsed)
            return False, f"Min spacing not met ({remaining}s remaining)"

    return True, ""


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_post(args):
    state = _load_state()
    ok, reason = _check_limits(state)
    if not ok:
        print(f"SKIP: {reason}")
        return

    # 1. Fetch market data
    print("Fetching market data...")
    market = _fetch_market_via_mcp()

    # 2. Draft post
    recent = [p["text"] for p in state.get("posts", [])[-5:]]
    print("Drafting post...")
    text, rationale = _draft_post(market, recent)

    # Proofread every numeric claim before any provider call.
    valid, validation_errors = _validate_post_text(text, market)
    if not valid:
        print(f"BLOCKED: proofreading failed: {', '.join(validation_errors)}", file=sys.stderr)
        return

    if len(text) > 280:
        print(f"WARN: Post is {len(text)} chars, truncating to 280")
        text = text[:277] + "..."

    # Duplicate check
    content_hash = hashlib.sha256(text.casefold().strip().encode()).hexdigest()[:16]
    recent_hashes = [p.get("content_hash") for p in state.get("posts", [])[-3:]]
    if content_hash in recent_hashes:
        print("SKIP: Duplicate or near-duplicate of recent post")
        return

    # 3. Publish
    result = _publish(text, dry_run=args.dry_run)

    # 4. Update state
    state["posts"].append({
        "text": text,
        "content_hash": content_hash,
        "rationale": rationale,
        "result": result,
        "posted_at": datetime.now(UTC).isoformat(),
    })
    state["posts"] = state["posts"][-50:]  # Keep last 50
    state["last_post_at"] = datetime.now(UTC).isoformat()
    state["daily_count"] = state.get("daily_count", 0) + 1
    _save_state(state)

    print(f"OK: {json.dumps(result, default=str)}")


def cmd_oauth_status(args):
    token = _load_token()
    if not token:
        print("No OAuth token found.")
        return
    access_token = _get_access_token()
    identity = _verify_identity(access_token)
    print(json.dumps({
        "status": "VALID",
        "username": identity.get("username"),
        "user_id": identity.get("id"),
        "expires_at": token.get("expires_at"),
        "scopes": token.get("granted_scopes", []),
    }, indent=2))


def cmd_refresh_token(args):
    _refresh_token()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Alpha Trading Floor — Minimal X Poster")
    sub = parser.add_subparsers(dest="command")

    p_post = sub.add_parser("post", help="Generate & publish a market post")
    p_post.add_argument("--dry-run", action="store_true", help="Preview without publishing")

    sub.add_parser("oauth-status", help="Show OAuth token status")
    sub.add_parser("refresh-token", help="Force OAuth token refresh")

    args = parser.parse_args()
    if args.command == "post":
        cmd_post(args)
    elif args.command == "oauth-status":
        cmd_oauth_status(args)
    elif args.command == "refresh-token":
        cmd_refresh_token(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
