# Phase 3 Market Data Findings

## 1) Existing providers

### Alpaca
Observed in:
- `alpaca-bot/v6.2-alpaca-paper.mjs`
- `alpaca-bot/scanners/alpaca_spy_0dte.py`
- `alpaca-bot/utils/alpaca-option-pricer.js`
- `alpaca-bot/swing-spreads/execution/alpaca.mjs`

What it does:
- Quotes / market snapshots for active trading workflows
- Intraday bar usage for scanners and pricing logic
- Options-related calculations in the paper/scanner stack

Authentication:
- API key + secret header-based auth (Alpaca-style)
- Existing scripts imply env-based credential injection

Data available:
- Equity quotes
- Intraday bars
- Some market/option pricing context
- No evidence in the legacy code of a native market breadth feed
- No evidence of a native VIX endpoint in the legacy stack

Quote freshness:
- Suitable for live scanning when credentials/feed are available
- Delayed vs real-time depends on Alpaca feed/entitlement

Intraday bar support:
- Yes, used by the scanner/pricer stack

Market breadth support:
- No native breadth source found in the inspected legacy Alpaca code

Sector data support:
- No native sector endpoint found
- Sector performance must be derived from sector ETF proxies if needed

Reusable code:
- Endpoint ideas and symbol conventions
- Scanner/pricing patterns

Do not reuse directly:
- Legacy strategy logic
- Scanner-specific heuristics
- Any direct script implementation details

Licensing / API limitations:
- Alpaca data entitlements matter
- IEX can be delayed; SIP access depends on subscription/permissions
- Native breadth/VIX coverage is limited or absent in the inspected code

---

### Robinhood
Observed in:
- `alpaca-bot/swing-spreads/execution/robinhood.mjs`
- `alpaca-bot/rh_direct_api.mjs`

What it does:
- Execution / account / direct API style integration
- Used as a trading-path integration, not a dedicated clean market-data layer

Authentication:
- Robinhood session / token style auth (legacy direct API usage)
- More brittle than Alpaca-style header auth

Data available:
- Account/execution oriented data
- Not a cleanly normalized market data feed in the inspected code

Quote freshness:
- Not enough evidence of a reliable, reusable real-time market-data SLA

Intraday bar support:
- No clean reusable bar interface identified

Market breadth support:
- No

Sector data support:
- No

Reusable code:
- Only limited auth/reference patterns, if any

Do not reuse directly:
- The direct API execution logic
- Session/token handling assumptions
- Any brittle private endpoint behavior

Licensing / API limitations:
- Robinhood direct/private APIs are undocumented and brittle
- Higher maintenance risk than Alpaca for a new normalized market data engine

---

### Stocktwits
- No dedicated Stocktwits market-data collector was found in the inspected Jarvis workspace.
- No reusable Stocktwits market feed was identified for Phase 3.

---

### Existing SPY / QQQ / monitor scripts
Observed in:
- `alpaca-bot/scanners/alpaca_spy_0dte.py`
- `alpaca-bot/v6.2-alpaca-paper.mjs`
- `alpaca-bot/preflight_audit.py`
- existing monitor/scheduler utilities in `.openclaw/workspace`

What they are useful for:
- Symbol conventions
- Scanner timing expectations
- General live-market operational patterns

What not to reuse directly:
- Strategy heuristics
- Execution side effects
- Script-specific state handling

---

## 2) Provider decision

### Selected initial provider: AlpacaMarketDataProvider
Reason:
- It is the cleanest existing market-data integration already present in Jarvis.
- It supports the two core primitives Phase 3 needs most: quotes and intraday bars.
- It is easier to normalize into a production data layer than the Robinhood execution path.
- It allows a clean adapter boundary without importing legacy scanner logic.

### Provider limitations documented up front
- Market breadth is not natively available in the inspected legacy Alpaca code.
- VIX is not guaranteed to be available from the same feed.
- Sector performance must be derived from sector ETF proxies when available.
- Quote freshness depends on feed entitlements and provider delay.
- If the provider cannot supply a field, the engine must return `None` or degrade quality explicitly.

## 3) Reuse guidance

### Reuse
- Alpaca auth/header conventions
- Symbol universe conventions for SPY/QQQ/DIA/IWM
- Intraday bar retrieval patterns

### Do not reuse
- Legacy scanner heuristics
- Any execution-side logic
- Robinhood private API behavior as a primary data backbone
- Any code path that fabricates market breadth, VIX, or sector data

## 4) Phase 3 implementation stance

- Clean provider adapter only
- Validated Pydantic domain models only
- No raw provider JSON in the core engine
- Optional capabilities must remain optional
- No fabricated values for unsupported data
