"""ClaudeProvider 单测：MockTransport 注入假响应，禁止真实联网、禁止读真实凭证。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from server.providers.claude import ClaudeProvider

FIXTURE = (
    Path(__file__).parent / "fixtures" / "claude_usage.json"
).read_text(encoding="utf-8")

FAKE_TOKEN = "sk-ant-test-fake"


def _write_credentials(tmp_path: Path, expires_at_ms: int | None = None) -> Path:
    if expires_at_ms is None:
        expires_at_ms = int(
            (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000
        )
    cred = tmp_path / "credentials.json"
    cred.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": FAKE_TOKEN,
                    "expiresAt": expires_at_ms,
                    "subscriptionType": "testplan",
                },
                "mcpOAuth": {"unrelated": "ignored"},
            }
        ),
        encoding="utf-8",
    )
    return cred


def _make_provider(
    tmp_path: Path,
    status_code: int = 200,
    body: str = FIXTURE,
    requests: list | None = None,
    expires_at_ms: int | None = None,
) -> ClaudeProvider:
    cred = _write_credentials(tmp_path, expires_at_ms)

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(status_code, text=body)

    return ClaudeProvider(
        credentials_path=str(cred),
        proxy="http://127.0.0.1:7890",
        transport=httpx.MockTransport(handler),
    )


async def test_ok_response_parsing(tmp_path):
    provider = _make_provider(tmp_path)
    usage = await provider.fetch()

    assert usage.status == "ok"
    assert usage.error is None
    assert usage.name == "Claude"
    assert usage.plan == "Testplan"

    assert [w.id for w in usage.windows] == ["5h", "week", "extra_credits"]
    w5h, wweek, wcredit = usage.windows
    # fixture 里具名字段 five_hour/seven_day 是冲突值 99.0/98.0，
    # 断言 11.0/22.0 即证明实现以 limits[] 为准
    assert w5h.used_pct == 11.0
    assert wweek.used_pct == 22.0
    assert wcredit.used_pct == 33.0

    assert w5h.label == "5 小时窗口"
    assert wweek.label == "周额度"
    assert wcredit.label == "额外用量 credit"

    assert w5h.resets_at is not None
    assert w5h.resets_at.tzinfo is not None  # tz-aware
    assert wcredit.resets_at is None
    assert usage.fetched_at.tzinfo is not None


async def test_http_401_auth_expired(tmp_path):
    usage = await _make_provider(tmp_path, status_code=401).fetch()
    assert usage.status == "auth_expired"
    assert usage.windows == []


async def test_http_500_error(tmp_path):
    usage = await _make_provider(tmp_path, status_code=500).fetch()
    assert usage.status == "error"
    assert "500" in usage.error


async def test_missing_credentials_file(tmp_path):
    provider = ClaudeProvider(
        credentials_path=str(tmp_path / "nonexistent" / "credentials.json"),
        proxy="http://127.0.0.1:7890",
    )
    usage = await provider.fetch()
    assert usage.status == "error"
    assert "凭证" in usage.error


async def test_expired_token_no_http_request(tmp_path):
    requests: list = []
    expired_ms = int(
        (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp() * 1000
    )
    provider = _make_provider(tmp_path, requests=requests, expires_at_ms=expired_ms)
    usage = await provider.fetch()
    assert usage.status == "auth_expired"
    assert requests == []  # 没有发起任何 HTTP 请求


async def test_unknown_kind_passthrough(tmp_path):
    payload = json.loads(FIXTURE)
    payload["limits"].append(
        {
            "kind": "weekly_wombat",
            "group": "weekly",
            "percent": 5,
            "severity": "normal",
            "resets_at": None,
            "scope": None,
            "is_active": False,
        }
    )
    usage = await _make_provider(tmp_path, body=json.dumps(payload)).fetch()
    ids = [w.id for w in usage.windows]
    assert "weekly_wombat" in ids
    w = next(w for w in usage.windows if w.id == "weekly_wombat")
    assert w.label == "weekly_wombat"
    assert w.used_pct == 5.0
    # 未知 weekly_* 排在 week 之后、extra_credits 之前
    assert ids == ["5h", "week", "weekly_wombat", "extra_credits"]


async def test_extra_usage_disabled_no_credit_window(tmp_path):
    payload = json.loads(FIXTURE)
    payload["extra_usage"]["is_enabled"] = False
    usage = await _make_provider(tmp_path, body=json.dumps(payload)).fetch()
    assert [w.id for w in usage.windows] == ["5h", "week"]


async def test_authorization_header_sent(tmp_path):
    requests: list = []
    usage = await _make_provider(tmp_path, requests=requests).fetch()
    assert usage.status == "ok"
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == f"Bearer {FAKE_TOKEN}"
    assert "anthropic-beta" not in requests[0].headers


async def test_limits_missing_is_error(tmp_path):
    payload = json.loads(FIXTURE)
    del payload["limits"]
    usage = await _make_provider(tmp_path, body=json.dumps(payload)).fetch()
    assert usage.status == "error"
    assert usage.windows == []


async def test_limits_null_is_error(tmp_path):
    payload = json.loads(FIXTURE)
    payload["limits"] = None
    usage = await _make_provider(tmp_path, body=json.dumps(payload)).fetch()
    assert usage.status == "error"
    assert usage.windows == []


async def test_limits_wrong_type_is_error(tmp_path):
    payload = json.loads(FIXTURE)
    payload["limits"] = "not-a-list"
    usage = await _make_provider(tmp_path, body=json.dumps(payload)).fetch()
    assert usage.status == "error"
    assert usage.windows == []


async def test_extra_credits_note_shows_money(tmp_path):
    """额外 credit 池除了百分比，还要给出金额（fixture: 3300/10000 分 → $33 / $100）。"""
    usage = await _make_provider(tmp_path).fetch()
    credit = next(w for w in usage.windows if w.id == "extra_credits")
    assert credit.note == "$33 / $100"
    # 其余窗口没有绝对量可显示
    assert all(w.note is None for w in usage.windows if w.id != "extra_credits")


async def test_credits_note_falls_back_to_extra_usage(tmp_path):
    """spend 缺失时退回 extra_usage 自带字段。"""
    payload = json.loads(FIXTURE)
    del payload["spend"]
    usage = await _make_provider(tmp_path, body=json.dumps(payload)).fetch()
    credit = next(w for w in usage.windows if w.id == "extra_credits")
    assert credit.note == "$33 / $100"


async def test_credits_note_none_when_amounts_unusable(tmp_path):
    """金额字段是脏数据时宁可不显示，也不显示错的金额。"""
    payload = json.loads(FIXTURE)
    payload["spend"] = {"used": {"amount_minor": None}, "limit": {}}
    payload["extra_usage"] = {"is_enabled": True, "utilization": 33.0}
    usage = await _make_provider(tmp_path, body=json.dumps(payload)).fetch()
    credit = next(w for w in usage.windows if w.id == "extra_credits")
    assert credit.note is None
    assert credit.used_pct == 33.0
