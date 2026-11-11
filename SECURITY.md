# Security Policy

## Scope

This repository contains the Alpha Trading Floor market-content engine and its guarded publication controls. It is not a credential store, account export, or live-authorization record.

## Never commit

- X, market-data, AI-provider, Redis, MySQL, Discord, email, or cloud credentials
- Passwords, API keys, bearer tokens, cookies, OAuth files, private keys, certificates, or signed URLs
- Database URLs containing passwords, database files, migration runtime state, logs, cycle journals, or publication records
- Personal customer data, contact lists, unredacted screenshots, generated media, or local scheduler state
- `.env` files, shell history, or copied environment dumps

## Safe configuration

Use a local secret manager or environment variables. Keep OAuth and runtime state under a local owner-only directory outside the repository. Keep `AUTO_POST_ENABLED=false` and `PUBLISH_MODE=DRY_RUN` during development and certification. Credentials must never be used as a reason to bypass a publication or safety gate.

## Pre-push checklist

- Inspect staged paths for environment files, databases, logs, caches, and runtime artifacts.
- Run `git diff --cached --check`.
- Scan staged content and reachable history for high-confidence credential patterns.
- Run available unit and migration-drift tests in an isolated development environment.
- Confirm that certification commands cannot publish and that the intended repository is private.

## Incident response

If a credential or private record is committed, stop using it, revoke or rotate it, remove it from current files and reachable history, and re-scan the remote repository. Never paste the exposed value into an issue, commit, or chat.
