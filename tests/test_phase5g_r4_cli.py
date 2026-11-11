from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.database.models import PublicationRecord
from alpha_market_engine.database.repository import Repository
from datetime import UTC, datetime

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "alpha_trading_floor.py")


def _clean(engine):
    assert engine.url.database == "alpha_market_test", f"CLI certification refused database={engine.url.database!r}"
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in ("publication_holds", "publication_records"):
            connection.execute(text(f"TRUNCATE TABLE `{table}`"))
        connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))


def _run(engine, *args):
    env = os.environ.copy(); env["DATABASE_URL"] = str(engine.url); env["X_EXPECTED_USERNAME"] = "AlphaTradingF"
    return subprocess.run([sys.executable, SCRIPT, *args], env=env, text=True, capture_output=True)


def _load_cli():
    spec = importlib.util.spec_from_file_location("alpha_cli_r4", SCRIPT); module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def test_operator_hold_clear_cli_manual_auth_and_platform_paths(mysql_test_engine, monkeypatch, capsys):
    _clean(mysql_test_engine); result = _run(mysql_test_engine, "hold", "--reason", "r4 manual hold"); assert result.returncode == 0
    result = _run(mysql_test_engine, "clear-hold"); assert result.returncode == 0; payload = json.loads(result.stdout); assert payload["clear_accepted"] is True and payload["remaining_hold"] == "CLEAR"
    session = sessionmaker(bind=mysql_test_engine, future=True)(); repo = Repository(session); repo.upsert_global_hold(state="AUTH_HOLD", reason="r4 auth"); session.close()
    cli = _load_cli(); monkeypatch.setenv("DATABASE_URL", str(mysql_test_engine.url)); monkeypatch.setattr(cli, "oauth_status", lambda: {"identity_match": False, "access_token_valid": False, "scopes": []})
    try: cli.cmd_clear_hold(SimpleNamespace()); assert False
    except RuntimeError as exc: assert str(exc) == "AUTH_CLEAR_REQUIRES_VERIFIED_IDENTITY"
    session = sessionmaker(bind=mysql_test_engine, future=True)(); assert Repository(session).get_global_hold().hold_state == "AUTH_HOLD"; session.close()
    cli = _load_cli(); monkeypatch.setenv("DATABASE_URL", str(mysql_test_engine.url)); monkeypatch.setattr(cli, "oauth_status", lambda: {"identity_verified": True, "identity_match": True, "scopes": ["tweet.write"], "access_token_valid": True}); cli.cmd_clear_hold(SimpleNamespace()); output = capsys.readouterr().out; assert json.loads(output)["clear_accepted"] is True
    session = sessionmaker(bind=mysql_test_engine, future=True)(); now = datetime.now(UTC); session.add(PublicationRecord(publication_request_id="r4-cli-unknown", idempotency_key="r4-cli-unknown-key", analysis_id="a", opportunity_id="o", opportunity_key="o", draft_id="d", draft_request_id="dr", platform="X", content_text="unknown", content_hash="r4-cli-unknown-hash", content_pattern_key="p", publish_mode="DRY_RUN", approval_status="APPROVE", decision="UNKNOWN", decision_reasons_json=[], state="UNKNOWN_PLATFORM_STATE", attempt_number=1, requested_at=now, source_detected_at=now)); session.commit(); repo = Repository(session); repo.upsert_global_hold(state="PLATFORM_STATE_HOLD", reason="unknown"); session.close()
    result = _run(mysql_test_engine, "clear-hold"); assert result.returncode != 0
    session = sessionmaker(bind=mysql_test_engine, future=True)(); session.query(PublicationRecord).filter_by(idempotency_key="r4-cli-unknown-key").one().state = "SUCCEEDED"; session.commit(); session.close(); result = _run(mysql_test_engine, "clear-hold"); assert result.returncode == 0
    session = sessionmaker(bind=mysql_test_engine, future=True)(); repo = Repository(session); repo.upsert_global_hold(state="SYSTEM_HEALTH_HOLD", reason="unhealthy"); session.close(); cli = _load_cli(); monkeypatch.setenv("DATABASE_URL", str(mysql_test_engine.url)); monkeypatch.setattr(cli, "oauth_status", lambda: {"identity_match": False, "access_token_valid": False, "scopes": []})
    try: cli.cmd_clear_hold(SimpleNamespace()); assert False
    except RuntimeError as exc: assert str(exc) == "SYSTEM_HEALTH_STILL_FAILING"
    session = sessionmaker(bind=mysql_test_engine, future=True)(); assert Repository(session).get_global_hold().hold_state == "SYSTEM_HEALTH_HOLD"; session.close(); monkeypatch.setattr(cli, "oauth_status", lambda: {"identity_verified": True, "identity_match": True, "scopes": ["tweet.write"], "access_token_valid": True}); cli.cmd_clear_hold(SimpleNamespace()); capsys.readouterr()
    session = sessionmaker(bind=mysql_test_engine, future=True)(); Repository(session).upsert_global_hold(state="DATABASE_HOLD", reason="database recovered pending clear"); session.close(); monkeypatch.setattr(cli, "oauth_status", lambda: {"identity_match": False, "access_token_valid": False, "scopes": []})
    try: cli.cmd_clear_hold(SimpleNamespace()); assert False
    except RuntimeError as exc: assert str(exc) == "DATABASE_STILL_UNAVAILABLE"
    monkeypatch.setattr(cli, "oauth_status", lambda: {"identity_verified": True, "identity_match": True, "scopes": ["tweet.write"], "access_token_valid": True}); cli.cmd_clear_hold(SimpleNamespace()); capsys.readouterr()
    mysql_test_engine.dispose()
