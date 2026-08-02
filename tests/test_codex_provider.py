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


PROXY_ENV_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
)


def test_subprocess_env_strips_all_proxy_vars_when_direct(monkeypatch):
    """proxy=None（直连）时，父环境的大小写代理变量都必须从子进程环境删除。"""
    for name in PROXY_ENV_VARS:
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    env = CodexProvider(proxy=None)._subprocess_env()
    for name in PROXY_ENV_VARS:
        assert name not in env


def test_subprocess_env_sets_proxy_when_configured(monkeypatch):
    """显式代理时：http/https 四项写入目标代理，all/no 四项不得从父环境漏入。"""
    for name in PROXY_ENV_VARS:
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    env = CodexProvider(proxy="http://127.0.0.1:7890")._subprocess_env()
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        assert env[name] == "http://127.0.0.1:7890"
    for name in ("all_proxy", "no_proxy", "ALL_PROXY", "NO_PROXY"):
        assert name not in env


class _FakeStdin:
    def __init__(self) -> None:
        self._closing = False

    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True

    async def wait_closed(self) -> None:
        pass


class _FakeProc:
    def __init__(self, stdout: asyncio.StreamReader) -> None:
        self.stdin = _FakeStdin()
        self.stdout = stdout
        self.stderr = None
        self.returncode: int | None = None

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


async def _run_rpc_with_captured_env(monkeypatch, proxy: str | None) -> dict:
    """捕获 asyncio.create_subprocess_exec 实际收到的 env 关键字参数。"""
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["env"] = kwargs["env"]
        reader = asyncio.StreamReader()
        reader.feed_data(
            b'{"id":1,"result":{}}\n'
            b'{"id":2,"result":{"rateLimits":{"primary":'
            b'{"usedPercent":10,"windowDurationMins":300,"resetsAt":null}}}}\n'
        )
        return _FakeProc(reader)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await CodexProvider(proxy=proxy)._rpc_call()
    return captured["env"]


async def test_exec_env_actually_stripped_when_direct(monkeypatch):
    """直连时，交给子进程的 env 字典里不得有任何代理变量（执行层断言）。"""
    for name in PROXY_ENV_VARS:
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    env = await _run_rpc_with_captured_env(monkeypatch, proxy=None)
    for name in PROXY_ENV_VARS:
        assert name not in env


async def test_exec_env_actually_carries_configured_proxy(monkeypatch):
    """显式代理时，子进程 env 必须带目标代理且不含 all/no 残留（执行层断言）。"""
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    env = await _run_rpc_with_captured_env(
        monkeypatch, proxy="http://127.0.0.1:7890"
    )
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        assert env[name] == "http://127.0.0.1:7890"
    for name in ("all_proxy", "no_proxy", "ALL_PROXY", "NO_PROXY"):
        assert name not in env


def test_defaults_are_generic():
    provider = CodexProvider()
    assert provider._command == "codex"
    assert provider._proxy is None
