# Alpha Trading Floor Market Content Engine

Alpha Trading Floor is a controlled market-research and content-production system. It combines market data, opportunity detection, drafting, validation, publication controls, reconciliation, and audience analytics in one Python service.

## What is included

- Market Brain and provider adapters for structured market observations.
- Content Opportunity Engine for ranking and deduplicating candidate topics.
- Drafting and validation with confidence, factual, quality, and safety gates.
- Publication control with DRY_RUN, MANUAL, and separately gated LIMITED_AUTO modes.
- MySQL migrations, durable publication jobs, retries, reconciliation, observability, and operator controls.
- X OAuth helpers and guarded publisher code that reads credentials only from runtime configuration.
- Regression tests, deployment templates, phase findings, and the operations runbook.

## Safe operating boundary

The repository defaults to:

```text
AUTO_POST_ENABLED=false
PUBLISH_MODE=DRY_RUN
```

Certification commands analyze, draft, validate, and report without publishing. OAuth configuration alone never activates automated publication. A first real post, identity verification, clear holds, healthy dependencies, and explicit authorization are required before any limited-auto workflow could be considered.

This is not a live trading or autonomous social-publishing authorization. Market data is research input, and every public claim must be reviewed for source freshness, numerical accuracy, disclosure, and audience context.

## Setup

Use Python 3.11 or newer and a local virtual environment. Install the project with its development dependencies, then run migrations only against an explicitly configured development or test database. Keep all provider credentials, database URLs, OAuth state, and runtime records outside Git.

The tracked deployment template is documentation and configuration scaffolding. It is not installed or enabled by this repository publication.

## Project layout

- `alpha_market_engine/`: application packages for market data, content, database, safety, production control, and publishers.
- `scripts/`: operator and certification entry points.
- `tests/`: unit, integration, migration, provider, safety, and publication-control tests.
- `deploy/`: secret-free launchd templates.
- `OPERATIONS.md`: operating modes, holds, incident handling, and readiness checks.
- `PHASE_*_FINDINGS.md`: research and implementation findings.

## Security

This public repository is a sanitized source publication. It excludes `.env` files, OAuth state, API tokens, database files, logs, generated videos, caches, virtual environments, and local runtime state. Read `SECURITY.md` before changing configuration or pushing code.
