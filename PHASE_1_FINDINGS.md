# PHASE 1 — Existing Jarvis Inspection

**Project:** Alpha Trading Floor Market Content Engine
**Date:** 2026-07-13 12:56:52 PDT

## What I inspected

I reviewed the current Jarvis workspace at:

- `$HOME/.openclaw/workspace`
- Key context files: `AGENTS.md`, `SOUL.md`, `USER.md`, and recent memory files
- Existing automation scripts in `scripts/`, `alpaca-bot/`, and `social-bots/`

## Existing architecture summary

### 1) Scheduler / orchestration
Jarvis already has a strong script-first orchestration layer:

- `scripts/token_safe_trading_scheduler.py`
  - Deterministic, no-LLM minute scheduler
  - File lock singleton
  - Job registry with `--daemon`, `--once`, `--list`
  - Alerting via iMessage/SMS
  - Uses existing Alpaca/Robinhood job scripts
- `scripts/system_monitor.py`
  - Zero-token market/profit/account/news monitor
  - Writes JSON state files for other processes
  - Uses Alpaca + Robinhood data sources
  - Has alert routing and append-only trail logging

### 2) Trading / market automation
The workspace already contains a large trading automation surface:

- `alpaca-bot/`
  - Alpaca paper and Robinhood helper scripts
  - Dry-run / backtest / monitor / preflight artifacts
  - Existing state and report files in JSON/JSONL/MD
- `alpaca-bot/v6.2-dryrun.mjs`
- `alpaca-bot/v6.2-alpaca-paper.mjs`
- `alpaca-bot/rh_*` helpers
- `alpaca-bot/scanners/`
- `alpaca-bot/swing-spreads/`

### 3) Social publishing infrastructure
There is already reusable publishing/image-generation logic:

- `social-bots/social_text_poster.py`
  - Page-config driven publishing
  - `.env` loading per page
  - Image generation and post image formatting
  - Reusable “publisher” style patterns
- `social-bots/llama/`, `social-bots/didu/`, `social-bots/lux/`
  - Generated image pipelines
  - Posted quote tracking
  - Reusable state files and logging patterns

### 4) Logging and state management
Jarvis already uses a practical file-based operational model:

- JSON / JSONL state files
- Daily logs
- Append-only trade trails
- Alert files
- Simple, deterministic job logs

### 5) Existing model/provider abstraction
I did **not** find a mature shared AI provider abstraction in the current workspace that already matches the new Alpha Trading Floor requirements.

### 6) Existing FastAPI / SQLAlchemy / MySQL / Redis / Celery
I did **not** find evidence of a current FastAPI service, SQLAlchemy ORM layer, MySQL schema, Redis cache layer, or Celery worker stack in the existing Jarvis workspace.

## Reusable components I can carry forward

These are the most useful existing pieces to reuse or mirror:

- Deterministic scheduler patterns from `scripts/token_safe_trading_scheduler.py`
- Zero-token market state / alerting patterns from `scripts/system_monitor.py`
- Stateful, file-backed operational logs and reports from `alpaca-bot/`
- Publisher/image workflow patterns from `social-bots/social_text_poster.py`
- Existing market/trading knowledge embedded in current scripts and JSON state files

## Gaps relative to the new project spec

The Alpha Trading Floor system still needs to be built from scratch for these core areas:

- FastAPI service and API endpoints
- MySQL schema + migrations
- Redis cache integration
- AI provider abstraction
- Market snapshot engine
- Regime detection engine
- Opportunity detection
- Fact validation / safety / duplicate detection / quality scoring
- X publisher using the official API
- Analytics and learning engine
- Testing harness for the new system

## Important environment note

The requested `/ssd` path is **not writable** in this environment (`/ssd` returned a read-only filesystem error).
I created the project workspace under:

- `$HOME/ssd`

## Bottom line

Jarvis already has good deterministic scheduler and content-publishing patterns, but the new Alpha Trading Floor engine will be a new service layer, not a thin patch on top of the current scripts.

The safest approach is:

1. Reuse scheduling / logging / alerting patterns where useful
2. Build a new clean package under `$HOME/ssd`
3. Keep the old Jarvis automation intact
4. Add the new Alpha Trading Floor system as a separate project
