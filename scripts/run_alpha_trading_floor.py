#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import signal
import time
from datetime import UTC, datetime

from dotenv import load_dotenv

from alpha_market_engine.production import DatabaseRunnerLease, LimitedAutoPolicy, MarketSessionCalendar, OperatingMode
from alpha_market_engine.production_runtime import build_runtime


def main() -> int:
    parser = argparse.ArgumentParser(description="Alpha Trading Floor production runner")
    parser.add_argument("--certify", action="store_true")
    parser.add_argument("--cycles", type=int, default=0)
    parser.add_argument("--interval", type=int, default=None)
    args = parser.parse_args()
    load_dotenv(override=False)
    runtime = build_runtime(certification=args.certify)
    policy = LimitedAutoPolicy.from_env()
    mode = OperatingMode.DRY_RUN if args.certify else OperatingMode.LIMITED_AUTO if policy.enabled else OperatingMode.DRY_RUN
    lease = DatabaseRunnerLease(runtime.repository)
    if not lease.acquire():
        runtime.close(); print("RUNNER_LEASE_CONFLICT"); return 2
    stopping = False
    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, stop); signal.signal(signal.SIGTERM, stop)
    calendar = MarketSessionCalendar()
    cycles = 0
    started = time.monotonic()
    next_market_cycle = started
    next_job_poll = started
    next_heartbeat = started
    target_cycles = args.cycles or 1
    try:
        while not stopping and (not args.certify or cycles < target_cycles):
            now_mono = time.monotonic()
            due = min(next_market_cycle, next_job_poll, next_heartbeat)
            if now_mono < due:
                time.sleep(min(due - now_mono, 1.0)); continue
            now = datetime.now(UTC)
            if now_mono >= next_heartbeat:
                if not lease.heartbeat(now): print("RUNNER_LEASE_LOST", flush=True); return 3
                next_heartbeat = now_mono + 30
            if now_mono >= next_market_cycle and (args.certify or calendar.is_active(now)):
                result = asyncio.run(runtime.orchestrator.run_cycle(now=now, mode=mode)); cycles += 1
                print(f"CYCLE cycle_id={result.cycle_id} outcome={result.outcome} market_active={calendar.is_active(now)} mode={mode.value}", flush=True)
                next_market_cycle = now_mono + (0 if args.certify else (args.interval or policy.analysis_interval_seconds))
            elif now_mono >= next_market_cycle:
                next_market_cycle = now_mono + (args.interval or policy.analysis_interval_seconds)
            if now_mono >= next_job_poll:
                if not args.certify: runtime.job_worker.run_due_jobs(max_jobs=10, now=now)
                next_job_poll = now_mono + 60
    finally:
        lease.release(); runtime.close()
    print("SHUTDOWN_CLEAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
