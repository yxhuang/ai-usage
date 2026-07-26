"""CodexProvider 单测：app-server 注入假 RPC，sessions 使用 tmp_path。"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from server.providers.codex import CodexProvider

FIXTURES = Path(__file__).parent / "fixtures"
APP_SERVER_PAYLOAD = json.loads(
    (FIXTURES / "codex_ratelimits_appserver.json").read_text(encoding="utf-8")
)
SESSION_LINES = (FIXTURES / "codex_session_ratelimits.jsonl").read_text(
    encoding="utf-8"
)
SESSION_TIMESTAMP = datetime.fromisoformat("2026-01-01T07:15:33.540Z")


def _write_session(tmp_path: Path, content: str = SESSION_LINES) -> Path:
    session = tmp_path / "2026" / "07" / "26" / "rollout-test.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(content, encoding="utf-8")
    return session


async def _rpc_ok() -> dict:
    return APP_SERVER_PAYLOAD


async def _rpc_fail() -> dict:
    raise FileNotFoundError("fake app-server command missing")


async def test_app_server_ok_week_window_and_plan(tmp_path):
    provider = CodexProvider(sessions_dir=str(tmp_path), rpc_call=_rpc_ok)
    usage = await provider.fetch()

    assert usage.status == "ok"
    assert usage.error is None
    assert usage.plan == "Testplan"
    assert [window.id for window in usage.windows] == ["week"]
    assert usage.windows[0].used_pct == 14.0
    assert usage.windows[0].label == "周额度"
    assert usage.windows[0].resets_at is not None
    assert usage.windows[0].resets_at.tzinfo is not None
    assert usage.windows[0].resets_at.utcoffset() is not None


async def test_secondary_window_sorted_before_week(tmp_path):
    payload = json.loads(json.dumps(APP_SERVER_PAYLOAD))
    payload["rateLimits"]["secondary"] = {
        "usedPercent": 12,
        "windowDurationMins": 300,
        "resetsAt": None,
    }

    async def rpc() -> dict:
        return payload

    usage = await CodexProvider(
        sessions_dir=str(tmp_path), rpc_call=rpc
    ).fetch()

    assert usage.status == "ok"
    assert [window.id for window in usage.windows] == ["5h", "week"]
    assert [window.label for window in usage.windows] == [
        "5 小时窗口",
        "周额度",
    ]
    assert [window.used_pct for window in usage.windows] == [12.0, 14.0]


async def test_rpc_reader_ignores_unrelated_notifications():
    reader = asyncio.StreamReader()
    reader.feed_data(
        b'{"jsonrpc":"2.0","method":"remoteControl/status/changed","params":{}}\n'
    )
    reader.feed_data(b'{"id":99,"result":{"unrelated":true}}\n')
    reader.feed_data(b'{"id":2,"result":{"rateLimits":{"planType":"testplan"}}}\n')
    reader.feed_eof()

    result = await CodexProvider._read_response(reader, response_id=2)
    assert result == {"rateLimits": {"planType": "testplan"}}


@pytest.mark.parametrize("failure", [TimeoutError(), FileNotFoundError()])
async def test_app_server_failure_falls_back_to_stale_session(tmp_path, failure):
    _write_session(tmp_path)

    async def rpc() -> dict:
        raise failure

    usage = await CodexProvider(
        sessions_dir=str(tmp_path), rpc_call=rpc
    ).fetch()

    assert usage.status == "stale"
    assert usage.error is None
    assert usage.plan == "Testplan"
    assert usage.windows[0].id == "week"
    assert usage.windows[0].used_pct == 14.0
    assert usage.fetched_at == SESSION_TIMESTAMP
    assert usage.fetched_at.tzinfo is not None


async def test_failure_threshold_skips_new_process_attempts(tmp_path):
    _write_session(tmp_path)
    calls = 0

    async def rpc() -> dict:
        nonlocal calls
        calls += 1
        raise FileNotFoundError()

    provider = CodexProvider(
        sessions_dir=str(tmp_path),
        app_server_max_failures=2,
        app_server_retry_after_seconds=3600,
        rpc_call=rpc,
    )

    assert (await provider.fetch()).status == "stale"
    assert (await provider.fetch()).status == "stale"
    assert (await provider.fetch()).status == "stale"
    assert calls == 2


async def test_no_sessions_and_app_server_failure_is_error(tmp_path):
    usage = await CodexProvider(
        sessions_dir=str(tmp_path), rpc_call=_rpc_fail
    ).fetch()

    assert usage.status == "error"
    assert usage.windows == []
    assert "app-server" in usage.error
    assert "sessions" in usage.error


async def test_unknown_window_minutes_uses_generic_rule(tmp_path):
    session_data = {
        "timestamp": "2026-07-26T08:00:00Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "primary": {
                    "used_percent": 25,
                    "window_minutes": 4320,
                    "resets_at": None,
                },
                "secondary": None,
                "plan_type": "team",
            },
        },
    }
    _write_session(tmp_path, json.dumps(session_data) + "\n")

    usage = await CodexProvider(
        sessions_dir=str(tmp_path), rpc_call=_rpc_fail
    ).fetch()

    assert usage.status == "stale"
    assert usage.plan == "Team"
    assert usage.windows[0].id == "72h"
    assert usage.windows[0].label == "72 小时窗口"
    assert usage.windows[0].used_pct == 25.0


async def test_newest_file_with_valid_record_wins(tmp_path):
    older = _write_session(tmp_path)
    newer = tmp_path / "new" / "rollout-new.jsonl"
    newer.parent.mkdir()
    newer.write_text(
        json.dumps(
            {
                "timestamp": "2026-07-26T09:00:00Z",
                "payload": {
                    "rate_limits": {
                        "primary": {
                            "used_percent": 18,
                            "window_minutes": 300,
                            "resets_at": None,
                        },
                        "plan_type": "pro",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    usage = await CodexProvider(
        sessions_dir=str(tmp_path), rpc_call=_rpc_fail
    ).fetch()

    assert usage.status == "stale"
    assert usage.plan == "Pro"
    assert usage.windows[0].id == "5h"
    assert usage.windows[0].used_pct == 18.0
