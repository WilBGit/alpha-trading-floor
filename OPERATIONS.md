# Alpha Trading Floor Operations

## Project and configuration

- Project: `$HOME/ssd/alpha-trading-floor`
- Local environment: `.env` (ignored; never commit it)
- Safe defaults: `AUTO_POST_ENABLED=false`, `PUBLISH_MODE=DRY_RUN`
- Runtime state: `~/.hermes/state/alpha_trading_floor/`
- OAuth token state: `~/.hermes/state/alpha_trading_floor_x_oauth.json`

## Checks

```bash
cd $HOME/ssd/alpha-trading-floor
env -u PYTHONPATH ../.venv/bin/pytest -q
env -u PYTHONPATH ../.venv/bin/pytest tests/test_migration_drift.py -q
env -u PYTHONPATH ../.venv/bin/python scripts/alpha_trading_floor.py status
env -u PYTHONPATH ../.venv/bin/python scripts/alpha_trading_floor.py oauth-status
env -u PYTHONPATH ../.venv/bin/python scripts/alpha_trading_floor.py health
env -u PYTHONPATH ../.venv/bin/python scripts/alpha_trading_floor.py jobs
```

## Operating modes

`OFF` disables market-content production. `DRY_RUN` executes analysis, drafting, validation, and eligibility without X publication. `MANUAL` requires explicit approval. `LIMITED_AUTO` requires both `AUTO_POST_ENABLED=true` and `PUBLISH_MODE=LIMITED_AUTO`, a verified `AlphaTradingF` identity, valid refreshable OAuth, a clear global hold, a healthy publication-critical system, and a certified first real post.

Do not use a `FULL_AUTO` mode.

## Manual controls

```bash
python scripts/alpha_trading_floor.py hold --reason "maintenance"
python scripts/alpha_trading_floor.py holds
python scripts/alpha_trading_floor.py clear-hold
python scripts/alpha_trading_floor.py run-cycle --dry-run
python scripts/alpha_trading_floor.py certify --cycles 2
```

A hold blocks limited-auto publication. `AUTH_HOLD` requires successful OAuth identity verification before clearing. `PLATFORM_STATE_HOLD` requires reconciliation of the ambiguous publication state. Never clear a hold to bypass a failed gate.

## Runner

```bash
python scripts/run_alpha_trading_floor.py --certify --cycles 2
python scripts/run_alpha_trading_floor.py
```

The runner uses the XNYS calendar, normal Pacific analysis window 06:30–12:55, early-close session boundaries, five-minute cadence, a process lease, and SIGINT/SIGTERM cleanup. It does not publish by itself; service wiring must provide the certified service graph.

## launchd

The tracked template is `deploy/com.alphatradingfloor.engine.plist`. It contains no secrets and defaults to the project runner's safe environment.

```bash
sh deploy/launchd.sh install
sh deploy/launchd.sh start
sh deploy/launchd.sh status
sh deploy/launchd.sh restart
sh deploy/launchd.sh stop
sh deploy/launchd.sh uninstall
```

Do not install until the final readiness gates pass. Run in DRY_RUN first.

## Incident response

- Disable posting immediately: set `AUTO_POST_ENABLED=false` and `PUBLISH_MODE=DRY_RUN`, then restart the runner.
- OAuth failure or identity mismatch: inspect `oauth-status`; use `hold --reason ...`; reauthorize; verify `AlphaTradingF`; only then clear the hold.
- `UNKNOWN_PLATFORM_STATE`: do not retry or publish; reconcile the publication and inspect `publication-status`.
- Database unavailable: stop the runner, preserve the publication hold, repair connectivity, run migrations/drift checks, then certify again.
- Stop the runner: `sh deploy/launchd.sh stop` or send SIGTERM.

## R3 safety invariants

- Publication is fail-closed when the database control-plane dependency is unavailable. The runtime enters `UNAVAILABLE/NOT_READY`; it does not claim a durable hold was stored until database access returns. On recovery, `DATABASE_HOLD` is persisted and requires an explicit policy-compliant clear.
- OAuth authentication, refresh, identity, and required-scope failures automatically activate `AUTH_HOLD` through `ProductionSafetyService`. Token values are never included in event details.
- Three persisted drafting-provider failures or three terminal X-provider failures inside the one-hour breaker window activate `SYSTEM_HEALTH_HOLD`. Failure timestamps are persisted explicitly in UTC metadata so restart durability is independent of MySQL session timezone. Eligibility blocks, duplicate suppression, spacing, cooldown, validation rejection, DRY_RUN, and kill-switch blocks do not count as provider failures.
- `RETRY_PUBLICATION` reconstructs the persisted draft and opportunity and passes the original persisted idempotency key through `ContentPublicationService`; it never invokes Market Brain, opportunity generation, or drafting. A retry blocked by canonical eligibility updates the existing publication decision rather than creating a second record.
- Unknown or ambiguous publication responses never trigger a blind resend. They persist `UNKNOWN_PLATFORM_STATE`, create/reuse reconciliation work, and activate `PLATFORM_STATE_HOLD`.
- Hold clearing is policy-driven: auth requires verified identity, expected username, and scopes; platform state requires zero unresolved unknowns; database/system holds require recovered health. The operator CLI calls `ProductionSafetyService` rather than updating hold rows directly.
- Shutdown invariant: before provider request, no provider call is made; after a known response, recovery uses the persisted publication/idempotency state; after an ambiguous response, reconciliation is mandatory and provider invocation count remains one.

## Timing and calendar policy

- XNYS session boundaries come from the installed exchange calendar, including early closes; production does not hard-code holiday or early-close rules.
- Market Brain analysis cadence is 300 seconds during the configured session window.
- Durable job polling is independent at 60 seconds.
- Runner lease heartbeat is 30 seconds with a 120-second database lease TTL.
- Healthy no-candidate audit cycles are retained, while repeated `NO_FRESH_OPPORTUNITY` events are coalesced within the one-hour window.

## Certification and readiness

```bash
env -u PYTHONPATH ../.venv/bin/pytest -q
env -u PYTHONPATH ../.venv/bin/pytest tests/test_migration_drift.py -q
env -u PYTHONPATH ../.venv/bin/python scripts/run_alpha_trading_floor.py --certify --cycles 2
```

`LIMITED_AUTO_READY=YES` means deterministic implementation gates and certification are complete; it does not activate publication. `LIMITED_AUTO_ACTIVE` remains `NO` until separately authorized. `AUTO_POST_ENABLED` remains false/unset and `PUBLISH_MODE` remains `DRY_RUN` during certification. `FIRST_REAL_POST_CERTIFIED` cannot be created by a manual or arbitrary test post.

## Operator health and hold clearing

```bash
python scripts/alpha_trading_floor.py health
python scripts/alpha_trading_floor.py hold --reason "maintenance"
python scripts/alpha_trading_floor.py clear-hold
```

`health` renders the canonical publication-critical component matrix and overall `READY`, `DEGRADED`, `NOT_READY`, or `STOPPED` state. `clear-hold` calls `ProductionSafetyService` and reports policy rejection instead of bypassing unresolved auth, database, system-health, or platform-state conditions.

```bash
git log --oneline -5
git status --short
git diff --check
```

Never print or commit credentials. Do not push without explicit authorization.

## Session summaries and publication audit

Cycle history is stored in `~/.hermes/state/alpha_trading_floor/cycles.json`. Publication records, reconciliation records, jobs, and metrics snapshots are stored in MySQL. The soft target of 6–8 posts is never forced; the hard maximum and minimum spacing remain authoritative. A single verified first real post is required before limited-auto readiness can become true.
