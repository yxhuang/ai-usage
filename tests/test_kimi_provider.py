"""KimiProvider 单测：MockTransport 注入假响应，禁止真实联网、禁止读真实凭证。

测试里的 key 一律是假值 sk-kimi-fake-for-test；
api_key_file 一律指向 tmp_path，绝不碰真实的 ~/.config/shell/secrets.sh。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from server.providers.kimi import KimiProvider

FIXTURE = (
    Path(__file__).parent / "fixtures" / "kimi_usages.json"
).read_text(encoding="utf-8")

FAKE_KEY = "sk-kimi-fake-for-test"
FAKE_KEY_ENV = "sk-kimi-fake-from-env"
FAKE_KEY_FILE = "sk-kimi-fake-from-file"


def _make_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int = 200,
    body: str = FIXTURE,
    requests: list | None = None,
    api_key: str | None = FAKE_KEY,
) -> KimiProvider:
    # 隔离环境：默认清掉环境变量，api_key_file 指向 tmp_path（默认不存在）
    monkeypatch.delenv("KIMI_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(status_code, text=body)

    return KimiProvider(
        api_key=api_key,
        api_key_file=str(tmp_path / "secrets.sh"),
        transport=httpx.MockTransport(handler),
    )


async def test_ok_response_parsing(tmp_path, monkeypatch):
    provider = _make_provider(tmp_path, monkeypatch)
    usage = await provider.fetch()

    assert usage.status == "ok"
    assert usage.error is None
    assert usage.id == "kimi"
    assert usage.name == "Kimi"
    assert usage.plan == "Purchase"

    # 两个窗口，5h 在前、week 在后；字符串数值换算成百分比
    assert [w.id for w in usage.windows] == ["5h", "week"]
    w5h, wweek = usage.windows
    assert w5h.label == "5 小时窗口"
    assert w5h.used_pct == 5.0
    assert wweek.label == "周额度"
    assert wweek.used_pct == 13.0

    assert w5h.resets_at is not None
    assert w5h.resets_at.tzinfo is not None  # tz-aware
    assert w5h.resets_at.utcoffset() is not None
    assert wweek.resets_at is not None
    assert wweek.resets_at.tzinfo is not None
    assert usage.fetched_at.tzinfo is not None

    # 账号身份信息不得进入 ProviderUsage
    assert "redacted" not in json.dumps(usage.to_dict())


async def test_zero_limit_used_pct_zero(tmp_path, monkeypatch):
    payload = json.loads(FIXTURE)
    payload["limits"][0]["detail"]["limit"] = "0"
    payload["usage"]["limit"] = "0"
    usage = await _make_provider(tmp_path, monkeypatch, body=json.dumps(payload)).fetch()
    assert usage.status == "ok"
    assert [w.used_pct for w in usage.windows] == [0.0, 0.0]


async def test_missing_limit_used_pct_zero(tmp_path, monkeypatch):
    payload = json.loads(FIXTURE)
    del payload["limits"][0]["detail"]["limit"]
    del payload["usage"]["limit"]
    usage = await _make_provider(tmp_path, monkeypatch, body=json.dumps(payload)).fetch()
    assert usage.status == "ok"
    assert [w.used_pct for w in usage.windows] == [0.0, 0.0]


@pytest.mark.parametrize("status_code", [401, 403])
async def test_http_401_403_auth_expired(tmp_path, monkeypatch, status_code):
    usage = await _make_provider(
        tmp_path, monkeypatch, status_code=status_code
    ).fetch()
    assert usage.status == "auth_expired"
    assert usage.windows == []
    assert "过期" in usage.error


async def test_http_500_error(tmp_path, monkeypatch):
    usage = await _make_provider(tmp_path, monkeypatch, status_code=500).fetch()
    assert usage.status == "error"
    assert usage.error == "HTTP 500"


async def test_no_key_anywhere_no_http_request(tmp_path, monkeypatch):
    requests: list = []
    provider = _make_provider(
        tmp_path, monkeypatch, requests=requests, api_key=None
    )
    usage = await provider.fetch()
    assert usage.status == "error"
    assert "未配置 Kimi API key" in usage.error
    assert requests == []  # 没有发起任何 HTTP 请求


async def test_key_extracted_from_file(tmp_path, monkeypatch):
    secrets = tmp_path / "secrets.sh"
    secrets.write_text(
        '# shell secrets\n'
        'export OTHER_TOKEN="unrelated"\n'
        f'export KIMI_API_KEY="{FAKE_KEY_FILE}"\n',
        encoding="utf-8",
    )
    requests: list = []
    provider = _make_provider(
        tmp_path, monkeypatch, requests=requests, api_key=None
    )
    usage = await provider.fetch()
    assert usage.status == "ok"
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == f"Bearer {FAKE_KEY_FILE}"


async def test_key_file_unquoted_and_single_quoted(tmp_path, monkeypatch):
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    for line in (
        f"KIMI_API_KEY={FAKE_KEY_FILE}",
        f"export KIMI_API_KEY='{FAKE_KEY_FILE}'",
    ):
        (tmp_path / "secrets.sh").write_text(line + "\n", encoding="utf-8")
        provider = KimiProvider(api_key_file=str(tmp_path / "secrets.sh"))
        assert provider._resolve_api_key() == FAKE_KEY_FILE


async def test_env_var_beats_file(tmp_path, monkeypatch):
    (tmp_path / "secrets.sh").write_text(
        f'export KIMI_API_KEY="{FAKE_KEY_FILE}"\n', encoding="utf-8"
    )
    monkeypatch.setenv("KIMI_API_KEY", FAKE_KEY_ENV)
    requests: list = []
    provider = _make_provider(
        tmp_path, monkeypatch, requests=requests, api_key=None
    )
    # _make_provider 会 delenv，在构造之后再设上
    monkeypatch.setenv("KIMI_API_KEY", FAKE_KEY_ENV)
    usage = await provider.fetch()
    assert usage.status == "ok"
    assert requests[0].headers["Authorization"] == f"Bearer {FAKE_KEY_ENV}"


async def test_config_key_beats_env(tmp_path, monkeypatch):
    requests: list = []
    provider = _make_provider(
        tmp_path, monkeypatch, requests=requests, api_key=FAKE_KEY
    )
    # _make_provider 会 delenv，在构造之后再设上
    monkeypatch.setenv("KIMI_API_KEY", FAKE_KEY_ENV)
    usage = await provider.fetch()
    assert usage.status == "ok"
    assert requests[0].headers["Authorization"] == f"Bearer {FAKE_KEY}"


async def test_unknown_duration_generic_rule(tmp_path, monkeypatch):
    payload = json.loads(FIXTURE)
    payload["limits"].append(
        {
            "window": {"duration": 1440, "timeUnit": "TIME_UNIT_MINUTE"},
            "detail": {
                "limit": "50",
                "used": "25",
                "remaining": "25",
                "resetTime": "2026-07-27T10:46:25.304440Z",
            },
        }
    )
    usage = await _make_provider(tmp_path, monkeypatch, body=json.dumps(payload)).fetch()
    assert usage.status == "ok"
    # 1440 分钟 = 24 小时，走通用规则；短窗口按时长升序排在 week 前
    assert [w.id for w in usage.windows] == ["5h", "24h", "week"]
    w24h = usage.windows[1]
    assert w24h.label == "24 小时窗口"
    assert w24h.used_pct == 50.0


async def test_no_proxy_and_authorization_header(tmp_path, monkeypatch):
    # 注：httpx 传入自定义 transport 时本就不加载环境代理（client._mounts 为空），
    # 所以本条只验证请求头与目标 URL；「环境代理被忽略」由下方
    # _CapturingClient 两条用例在构造参数层面验证
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    requests: list = []
    provider = _make_provider(tmp_path, monkeypatch, requests=requests)
    usage = await provider.fetch()
    assert usage.status == "ok"
    assert len(requests) == 1
    request = requests[0]
    assert request.url.host == "api.kimi.com"
    assert request.url.path == "/coding/v1/usages"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert request.headers["Accept"] == "application/json"


class _CapturingClient:
    """记录构造参数的 httpx.AsyncClient 替身：验证网络层配置而非请求本身。

    必须走这条路：传 MockTransport 时 httpx 不加载环境代理，
    即使删掉 trust_env=False 也测不出来。
    """

    captured: dict = {}

    def __init__(self, **kwargs):
        _CapturingClient.captured = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        return httpx.Response(200, text=FIXTURE)


async def test_no_proxy_ignores_env_proxy(tmp_path, monkeypatch):
    # 环境里有代理变量时，默认（proxy=None）必须直连：trust_env=False 且不传代理
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingClient)
    provider = KimiProvider(api_key=FAKE_KEY, api_key_file=None)
    usage = await provider.fetch()
    assert usage.status == "ok"
    assert _CapturingClient.captured.get("trust_env") is False
    assert not _CapturingClient.captured.get("proxy")


async def test_explicit_proxy_passed_to_client(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingClient)
    provider = KimiProvider(
        api_key=FAKE_KEY, api_key_file=None, proxy="http://127.0.0.1:7890"
    )
    usage = await provider.fetch()
    assert usage.status == "ok"
    assert _CapturingClient.captured.get("proxy") == "http://127.0.0.1:7890"
    assert _CapturingClient.captured.get("trust_env") is False


async def test_network_error_sanitized(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection refused to {FAKE_KEY}")

    provider = KimiProvider(
        api_key=FAKE_KEY,
        api_key_file=str(tmp_path / "secrets.sh"),
        transport=httpx.MockTransport(handler),
    )
    usage = await provider.fetch()
    assert usage.status == "error"
    # 异常正文可能夹带 key，error 文案只留异常类型名
    assert FAKE_KEY not in usage.error
    assert usage.error == "网络错误: ConnectError"


async def test_api_key_file_none_no_crash_no_request(tmp_path, monkeypatch):
    """api_key_file=None（默认）不得 Path(None) 崩溃，直接跳过文件来源。"""
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    requests: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=FIXTURE)

    provider = KimiProvider(
        api_key=None,
        api_key_file=None,
        transport=httpx.MockTransport(handler),
    )
    usage = await provider.fetch()
    assert usage.status == "error"
    assert "未配置 Kimi API key" in usage.error
    assert "secrets.sh" not in usage.error
    assert requests == []


async def test_api_key_file_none_with_explicit_key_ok(tmp_path, monkeypatch):
    requests: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=FIXTURE)

    provider = KimiProvider(
        api_key=FAKE_KEY,
        api_key_file=None,
        transport=httpx.MockTransport(handler),
    )
    usage = await provider.fetch()
    assert usage.status == "ok"
    assert requests[0].headers["Authorization"] == f"Bearer {FAKE_KEY}"


async def test_no_key_message_under_systemd(monkeypatch):
    """以 systemd 服务运行时，必须明说环境变量这条路走不通。"""
    monkeypatch.setenv("INVOCATION_ID", "deadbeef")
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    provider = KimiProvider()
    usage = await provider.fetch()
    assert usage.status == "error"
    assert "systemd" in usage.error
    assert "api_key_file" in usage.error


async def test_no_key_message_in_plain_shell(monkeypatch):
    """普通进程里环境变量是可用选项，提示中应保留它。"""
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    provider = KimiProvider()
    usage = await provider.fetch()
    assert usage.status == "error"
    assert "systemd" not in usage.error
    assert "KIMI_API_KEY" in usage.error


async def test_key_resolution_priority_unchanged(monkeypatch, tmp_path):
    """回归护栏：优先级仍是 api_key → 环境变量 → 文件，本次只改文案。"""
    monkeypatch.setenv("KIMI_API_KEY", "sk-from-env")
    key_file = tmp_path / "secrets.sh"
    key_file.write_text('export KIMI_API_KEY="sk-from-file"\n', encoding="utf-8")
    assert KimiProvider(api_key="sk-direct", api_key_file=str(key_file))._resolve_api_key() == "sk-direct"
    assert KimiProvider(api_key_file=str(key_file))._resolve_api_key() == "sk-from-env"
    monkeypatch.delenv("KIMI_API_KEY")
    assert KimiProvider(api_key_file=str(key_file))._resolve_api_key() == "sk-from-file"
