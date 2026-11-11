# PHASE 3.6R Robinhood Tool Findings

## tool inventory

Discovered Robinhood market-data tools used for this phase:

- `mcp__robinhood_trading__get_equity_quotes`
- `mcp__robinhood_trading__get_equity_historicals`

The provider allowlist rejects all other Robinhood tool families for safety.

## read-only allowlist

Allowed:

- `mcp__robinhood_trading__get_equity_quotes`
- `mcp__robinhood_trading__get_equity_historicals`

Rejected by prefix:

- `mcp__robinhood_trading__add_`
- `mcp__robinhood_trading__buy_`
- `mcp__robinhood_trading__cancel_`
- `mcp__robinhood_trading__create_`
- `mcp__robinhood_trading__deposit_`
- `mcp__robinhood_trading__modify_`
- `mcp__robinhood_trading__move_`
- `mcp__robinhood_trading__place_`
- `mcp__robinhood_trading__sell_`
- `mcp__robinhood_trading__submit_`
- `mcp__robinhood_trading__trade_`
- `mcp__robinhood_trading__transfer_`
- `mcp__robinhood_trading__update_`
- `mcp__robinhood_trading__withdraw_`

## safety design

- Read-only Robinhood market-data tools only.
- Order / trade / cash movement / cancel / modify / transfer tool families are blocked.
- Normalized outputs are UTC-aware.
- No credentials are printed or stored in source.
- Live calls are treated as market-data only; no trades, no orders, no cancellations, no position changes.

## quote certification

Three live Robinhood quote samples were captured for `SPY`, `QQQ`, `DIA`, and `IWM`.

Sampling window (UTC):

- Sample 1: `2026-07-14T00:36:56.537165+00:00` → `2026-07-14T00:37:20.463425+00:00` (`23.93s`)
- Sample 2: `2026-07-14T00:37:47.486155+00:00` → `2026-07-14T00:38:15.749131+00:00` (`28.26s`)
- Sample 3: `2026-07-14T00:38:36.301063+00:00` → `2026-07-14T00:39:01.297413+00:00` (`25.00s`)

### SPY quote results

| Sample | Price | Quote timestamp | Bid | Ask | Event age (s) |
|---|---:|---|---:|---:|---:|
| 1 | 749.100000 | 2026-07-13T19:59:59.983271+00:00 | 748.280000 | 748.400000 | 16640.480 |
| 2 | 749.100000 | 2026-07-13T19:59:59.983271+00:00 | 748.100000 | 748.250000 | 16695.766 |
| 3 | 749.100000 | 2026-07-13T19:59:59.983271+00:00 | 748.050000 | 748.130000 | 16741.314 |

Observed across samples:

- last-trade price stayed stable
- quote timestamp stayed stable
- bid/ask moved between samples
- timestamp is timezone-aware UTC
- previous close stayed available

### QQQ quote results

| Sample | Price | Quote timestamp | Bid | Ask | Event age (s) |
|---|---:|---|---:|---:|---:|
| 1 | 711.710000 | 2026-07-13T19:59:59.943010+00:00 | 712.230000 | 712.330000 | 16640.520 |
| 2 | 711.710000 | 2026-07-13T19:59:59.943010+00:00 | 711.870000 | 711.990000 | 16695.806 |
| 3 | 711.710000 | 2026-07-13T19:59:59.943010+00:00 | 711.700000 | 711.820000 | 16741.354 |

### DIA quote results

| Sample | Price | Quote timestamp | Bid | Ask | Event age (s) |
|---|---:|---|---:|---:|---:|
| 1 | 524.450000 | 2026-07-13T19:59:59.229687+00:00 | 523.100000 | 523.200000 | 16641.234 |
| 2 | 524.450000 | 2026-07-13T19:59:59.229687+00:00 | 523.040000 | 523.120000 | 16696.519 |
| 3 | 524.450000 | 2026-07-13T19:59:59.229687+00:00 | 523.000000 | 523.080000 | 16742.068 |

### IWM quote results

| Sample | Price | Quote timestamp | Bid | Ask | Event age (s) |
|---|---:|---|---:|---:|---:|
| 1 | 293.460000 | 2026-07-13T19:59:59.904786+00:00 | 292.770000 | 292.850000 | 16640.559 |
| 2 | 293.460000 | 2026-07-13T19:59:59.904786+00:00 | 292.710000 | 292.770000 | 16695.844 |
| 3 | 293.460000 | 2026-07-13T19:59:59.904786+00:00 | 292.620000 | 292.730000 | 16741.393 |

## timestamp semantics

Robinhood quote normalization uses the venue last-trade timestamp when available.

Evidence:

- `validation_metadata.venue_last_trade_time` was present in live quote payloads.
- The normalized UTC quote timestamp matched that venue trade timestamp to sub-microsecond rounding.
- `current_price_source` was `last_trade_price` for all sampled symbols.

Canonical price/timestamp pair:

- `last_trade_price`
- `last_trade_timestamp` derived from the same last-trade event

The adapter does **not** mix a last-trade price with a bid/ask snapshot timestamp.

## timestamp reliability

**SUPPORTED**

Reason:

- timestamps are timezone-aware UTC
- timestamps are stable and normalized
- quote timestamps are grounded in `venue_last_trade_time`

## request latency

Robinhood polling latencies for the three quote samples:

- min: `23926.36 ms`
- median: `24996.45 ms`
- max: `28263.08 ms`

Separate Alpaca request latency observed during comparison:

- SPY + QQQ quote request: `226.06 ms`

Conclusion:

- Robinhood is usable, but the CLI bridge latency is high.
- Request latency must be separated from market-event age.

## previous close support

### Robinhood

Supported for all sampled symbols.

Latest sampled values:

- SPY: `749.170000`
- QQQ: `711.740000`
- DIA: `524.470000`
- IWM: `293.480000`

### Alpaca IEX

Not available in this run for SPY/QQQ quote payloads (`previous_close = null`).

## exact percentage claims

Robinhood exact percentage changes, computed with `Decimal`:

| Symbol | Current price | Previous close | Exact % |
|---|---:|---:|---:|
| SPY | 749.100000 | 749.170000 | -0.009343673665523178984743115715% |
| QQQ | 711.710000 | 711.740000 | -0.004215022339618399977519880855% |
| DIA | 524.450000 | 524.470000 | -0.003813373500867542471447365912% |
| IWM | 293.460000 | 293.480000 | -0.006814774430966335014311026305% |

Rounded display values are approximately `0.00%` for all four symbols.

## intraday bar support

Robinhood full regular-session 1-minute bars were successfully retrieved for:

- SPY
- QQQ

## bar timeframe

- Robinhood: `1m`
- Alpaca: `1Min`

## full session retrieval

Reference session:

- `2026-07-13T13:30:00+00:00` → `2026-07-13T20:00:00+00:00`

Expected regular-session bars:

- `390`

Robinhood full-session results:

- SPY: `390` bars
- QQQ: `390` bars

Both symbols had:

- `0` duplicates
- `0` out-of-order bars
- `0` malformed bars
- `0` missing minutes
- `100%` coverage
- `largest contiguous gap = 0`

## SPY bar quality

**GOOD**

Details:

- raw bars available: `390`
- regular-session filtered bars: `390`
- first timestamp: `2026-07-13T13:30:00+00:00`
- last timestamp: `2026-07-13T19:59:00+00:00`
- coverage: `100%`
- malformed bars: `0`

## QQQ bar quality

**GOOD**

Details:

- raw bars available: `390`
- regular-session filtered bars: `390`
- first timestamp: `2026-07-13T13:30:00+00:00`
- last timestamp: `2026-07-13T19:59:00+00:00`
- coverage: `100%`
- malformed bars: `0`

## SPY session VWAP

- session date: `2026-07-13`
- bar count: `390`
- coverage: `100%`
- largest gap: `0`
- total volume represented: `15638806`
- VWAP method: `TYPICAL_PRICE_APPROXIMATION`
- VWAP quality: `GOOD`
- session VWAP: `750.5284397335789786850309001`

Classification:

- `COMPLETED_SESSION_VWAP`

## QQQ session VWAP

- session date: `2026-07-13`
- bar count: `390`
- coverage: `100%`
- largest gap: `0`
- total volume represented: `14256962`
- VWAP method: `TYPICAL_PRICE_APPROXIMATION`
- VWAP quality: `GOOD`
- session VWAP: `714.1673815176122374458176995`

Classification:

- `COMPLETED_SESSION_VWAP`

## VWAP claims

**SUPPORTED**

Reason:

- full regular-session 1-minute bars are available
- session VWAP can be computed deterministically
- the adapter uses a typical-price approximation because per-bar VWAP is unavailable

## OR5

### SPY

- high: `753.910000`
- low: `752.400000`
- range size: `1.510000`
- range percent: `0.2006911217437533227006911217%`
- expected bars: `5`
- actual bars: `5`
- coverage: `GOOD`

### QQQ

- high: `718.430000`
- low: `715.925000`
- range size: `2.505000`
- range percent: `0.3498969864161748786534902399%`
- expected bars: `5`
- actual bars: `5`
- coverage: `GOOD`

## OR15

### SPY

- high: `753.910000`
- low: `752.400000`
- range size: `1.510000`
- range percent: `0.2006911217437533227006911217%`
- expected bars: `15`
- actual bars: `15`
- coverage: `GOOD`

### QQQ

- high: `718.430000`
- low: `713.950000`
- range size: `4.480000`
- range percent: `0.6274949226136284053505147419%`
- expected bars: `15`
- actual bars: `15`
- coverage: `GOOD`

## OR30

### SPY

- high: `753.910000`
- low: `751.920000`
- range size: `1.990000`
- range percent: `0.2646558144483455686775188850%`
- expected bars: `30`
- actual bars: `30`
- coverage: `GOOD`

### QQQ

- high: `718.430000`
- low: `713.150000`
- range size: `5.280000`
- range percent: `0.7403771997475986819042277221%`
- expected bars: `30`
- actual bars: `30`
- coverage: `GOOD`

## opening range analysis

**SUPPORTED**

Reason:

- 5, 15, and 30 minute opening ranges are fully covered for SPY and QQQ
- the end timestamp is treated as exclusive

## fast momentum alerts

**UNSUPPORTED**

Reason:

- Robinhood is polling-based, not streaming
- quote request latency is ~24–28 seconds per 4-symbol sample
- event age was stale at measurement time because the market session was already closed

## Robinhood vs Alpaca IEX

### Robinhood

SPY:

- current price: `749.100000`
- current price timestamp: `2026-07-13T19:59:59.983271+00:00`
- previous close: available
- latest 1-minute bar timestamp: `2026-07-13T19:59:00+00:00`
- regular-session bar coverage: `100%`

QQQ:

- current price: `711.710000`
- current price timestamp: `2026-07-13T19:59:59.943010+00:00`
- previous close: available
- latest 1-minute bar timestamp: `2026-07-13T19:59:00+00:00`
- regular-session bar coverage: `100%`

Practical update characteristics:

- polling latency: high
- timestamp reliability: good
- regular-session completeness: strong
- previous close availability: strong

### Alpaca IEX

SPY:

- current price: `752.39`
- quote timestamp: `2026-07-13T20:00:00.004299+00:00`
- previous close: **not available** (`null`)
- raw 1-minute bars: `390`
- regular-session filtered bars: `385`
- first raw timestamp: `2026-07-13T12:00:00+00:00`
- regular-session latest bar: `2026-07-13T19:54:00+00:00`
- regular-session coverage: `98.71794871794871794871794872%`

QQQ:

- current price: `710.855`
- quote timestamp: `2026-07-13T20:49:44.537838+00:00`
- previous close: **not available** (`null`)
- raw 1-minute bars: `379`
- regular-session filtered bars: `370`
- first raw timestamp: `2026-07-13T12:24:00+00:00`
- regular-session latest bar: `2026-07-13T19:59:00+00:00`
- regular-session coverage: `94.87179487179487179487179487%`

Practical update characteristics:

- quote request latency: `226.06 ms`
- quote timestamp freshness: better than Robinhood in this run
- previous-close availability: weaker than Robinhood
- regular-session bar completeness: weaker than Robinhood

### comparison summary

- Robinhood: better for previous close + complete regular-session bars
- Alpaca IEX: much faster request/response and fresher quote timestamps in this run
- Neither source alone was perfect for all needs
- Cross-validation is the strongest pattern here

## capability matrix

| Capability | Status | Technical reason |
|---|---|---|
| GENERAL_MARKET_RECAP | SUPPORTED | Robinhood returns reliable quotes, previous close, and full regular-session bars. |
| INTRADAY_COMMENTARY | CONDITIONAL | Works for commentary and recap, but the transport is polling-based and not streaming. |
| EXACT_PERCENTAGE_CLAIMS | SUPPORTED | Robinhood provides a canonical current price and validated previous close. |
| VWAP_CLAIMS | SUPPORTED | Full regular-session bars are available; VWAP can be computed deterministically. |
| FAST_MOMENTUM_ALERTS | UNSUPPORTED | Request latency is too high for short-lived intraday momentum alerts. |
| OPENING_RANGE_ANALYSIS | SUPPORTED | OR5/OR15/OR30 are fully covered for SPY and QQQ. |

## provider recommendation

**OPTION C — Robinhood + Alpaca cross-validation**

Reason:

- Robinhood is strong on previous close and full regular-session bars.
- Alpaca IEX is much faster and provides fresher quote timestamps in this run.
- The two sources complement each other well.

SIP still required:

- **RECOMMENDED BUT NOT REQUIRED**

Reason:

- SIP would improve canonicality and latency consistency.
- The evidence here does not justify mandating SIP as a hard requirement.
- Robinhood + Alpaca IEX is already a workable cross-validation setup.

## known limitations

- Robinhood quote polling is slow relative to Alpaca.
- Robinhood event age was stale at measurement time because the market session was already closed.
- Alpaca quote payloads in this run did not include previous close.
- Alpaca regular-session bar coverage was incomplete for SPY and QQQ.
- Robinhood CLI JSON extraction had to be hardened to tolerate pretty output and surrounding commentary.

## files created

- `PHASE_3_6R_ROBINHOOD_TOOL_FINDINGS.md`

## files modified

- `alpha_market_engine/market/providers/robinhood.py`
- `tests/test_robinhood_provider.py`
- `PHASE_3_6R_ROBINHOOD_TOOL_FINDINGS.md`

## tests run

- `env -u PYTHONPATH ../.venv/bin/pytest -q`
- `env -u PYTHONPATH ../.venv/bin/pytest tests/test_market_live_robinhood.py -q`

## test result

**PASS**

- Full suite passed.
- Robinhood live opt-in tests were present but skipped because they are opt-in only in this environment.

## issues

- Robinhood quote bridge latency is high, so the provider is not suitable for fast momentum alerts.
- The adapter parser was hardened for pretty CLI output, which fixed a real live certification failure path.
- Alpaca timeframe must be passed as `1Min` (not `1m`) for this provider.

## next phase

- Phase 4 not started.
- No trades executed.
- No X content generated or published.
