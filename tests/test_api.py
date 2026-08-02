"""API 单测：TestClient 覆盖 /api/summary、/api/refresh、/api/vscode-hook、/。

通过 monkeypatch 注入假 provider 与 tmp_path 缓存，不碰真实凭证、不联真网。

写操作端点过 require_local_ui，所以 TestClient 的 base_url 必须是回环地址
（否则 Host 头是 testserver，一律 403），且要带 MUTATE 里的两个头。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import server.main as main_mod
from server.cache import Cache
from server.config import Config
from server.providers.base import ProviderUsage, UsageWindow

T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _usage(pid: str, fetched_at: datetime) -> ProviderUsage:
    return ProviderUsage(
        id=pid,
        name=pid.capitalize(),
        plan=None,
        windows=[
            UsageWindow(id="5h", label="5 小时窗口", used_pct=1.0, resets_at=None)
        ],
        status="ok",
        error=None,
        fetched_at=fetched_at,
    )


class FakeProvider:
    def __init__(self, pid: str, fetched_at: datetime) -> None:
        self.id = pid
        self._fetched_at = fetched_at
        self.calls = 0

    async def fetch(self) -> ProviderUsage:
        self.calls += 1
        return _usage(self.id, self._fetched_at)


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def _make(providers) -> TestClient:
        cfg = Config()
        cfg.providers["claude"].enabled = False  # 双保险：绝不构造真 ClaudeProvider
        monkeypatch.setattr(
            main_mod, "Cache", lambda: Cache(tmp_path / "cache.json")
        )
        monkeypatch.setattr(main_mod, "build_providers", lambda c: providers)
        return TestClient(
            main_mod.create_app(cfg), base_url="http://127.0.0.1:8788"
        )

    return _make


# 浏览器对同源的非 GET 请求会自动带 Origin；自定义头由前端显式加
MUTATE = {
    "X-Requested-By": "ai-usage-panel",
    "Origin": "http://127.0.0.1:8788",
}


def _three_providers():
    # 故意乱序，验证输出按 claude → codex → kimi
    return [
        FakeProvider("kimi", T0 + timedelta(seconds=20)),
        FakeProvider("codex", T0 + timedelta(seconds=10)),
        FakeProvider("claude", T0),
    ]


def test_summary_order_and_updated_at(make_client):
    client = make_client(_three_providers())
    resp = client.post("/api/refresh?provider=all", headers=MUTATE)
    assert resp.status_code == 200
    data = resp.json()
    assert [p["id"] for p in data["providers"]] == ["claude", "codex", "kimi"]
    assert data["updated_at"] == (T0 + timedelta(seconds=20)).isoformat()

    resp2 = client.get("/api/summary")
    assert resp2.status_code == 200
    assert resp2.json() == data


def test_refresh_all_calls_every_provider(make_client):
    providers = _three_providers()
    client = make_client(providers)
    resp = client.post("/api/refresh?provider=all", headers=MUTATE)
    assert resp.status_code == 200
    for p in providers:
        assert p.calls >= 1


def test_refresh_single_provider(make_client):
    providers = _three_providers()
    client = make_client(providers)
    resp = client.post("/api/refresh?provider=claude", headers=MUTATE)
    assert resp.status_code == 200
    claude = next(p for p in providers if p.id == "claude")
    assert claude.calls >= 1


def test_refresh_unknown_provider_400(make_client):
    client = make_client([])
    resp = client.post("/api/refresh?provider=nope", headers=MUTATE)
    assert resp.status_code == 400


def test_summary_empty(make_client):
    client = make_client([])
    resp = client.get("/api/summary")
    assert resp.status_code == 200
    assert resp.json() == {"updated_at": None, "providers": []}


def test_index_returns_html(make_client):
    client = make_client([])
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<html" in resp.text


def test_lifespan_starts_and_stops_cleanly(make_client):
    providers = _three_providers()
    client = make_client(providers)
    with client:  # 进入 lifespan：poller 启动；退出：cancel 并 await
        resp = client.get("/api/summary")
        assert resp.status_code == 200


# ---- 安全：写操作端点的三道校验 ----


def test_security_headers_on_every_response(make_client):
    client = make_client(_three_providers())
    resp = client.get("/api/summary")
    assert resp.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert resp.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="什么都不带"),
        pytest.param({"Origin": "http://127.0.0.1:8788"}, id="缺自定义头"),
        pytest.param({"X-Requested-By": "ai-usage-panel"}, id="缺 Origin"),
        pytest.param(
            {"X-Requested-By": "wrong", "Origin": "http://127.0.0.1:8788"},
            id="自定义头值不对",
        ),
        pytest.param(
            {"X-Requested-By": "ai-usage-panel", "Origin": "null"},
            id="Origin 为 null",
        ),
        pytest.param(
            {"X-Requested-By": "ai-usage-panel", "Origin": "https://evil.example"},
            id="Origin 是外站",
        ),
    ],
)
def test_refresh_rejects_bad_headers(make_client, headers):
    providers = _three_providers()
    client = make_client(providers)
    resp = client.post("/api/refresh?provider=all", headers=headers)
    assert resp.status_code == 403
    # 关键：不只是被拒，副作用一次都没发生
    assert all(p.calls == 0 for p in providers)


def test_bad_host_rejected(make_client):
    client = make_client(_three_providers())
    resp = client.post(
        "/api/refresh?provider=all",
        headers={**MUTATE, "Host": "evil.example"},
    )
    assert resp.status_code == 403


def test_x_forwarded_host_is_not_trusted(make_client):
    """伪造 X-Forwarded-Host 不能把一个坏 Host 洗白。"""
    client = make_client(_three_providers())
    resp = client.post(
        "/api/refresh?provider=all",
        headers={
            **MUTATE,
            "Host": "evil.example",
            "X-Forwarded-Host": "127.0.0.1:8788",
        },
    )
    assert resp.status_code == 403


def test_preflight_gets_no_cors_permission(make_client):
    """真实预检报文：服务端不给任何 CORS 许可，浏览器就不会发出实际的 PUT。"""
    client = make_client(_three_providers())
    resp = client.options(
        "/api/vscode-hook",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type,x-requested-by",
        },
    )
    allow_origin = resp.headers.get("access-control-allow-origin")
    assert allow_origin != "https://evil.example"
    assert "PUT" not in (resp.headers.get("access-control-allow-methods") or "")


# ---- 跟随编辑器启动的开关 ----


@pytest.fixture
def hook_home(tmp_path, monkeypatch):
    """把开关的标志文件挪进 tmp_path，绝不碰真实的 ~/.config。"""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path


def test_hook_defaults_to_enabled(make_client, hook_home):
    client = make_client(_three_providers())
    body = client.get("/api/vscode-hook").json()
    assert body["enabled"] is True
    # 只读查询不该凭空造出文件
    assert not (hook_home / "config" / "ai-usage").exists()


def test_hook_toggle_round_trip(make_client, hook_home):
    client = make_client(_three_providers())
    flag = hook_home / "config" / "ai-usage" / "vscode-hook.disabled"

    off = client.put("/api/vscode-hook", json={"enabled": False}, headers=MUTATE)
    assert off.status_code == 200
    assert off.json()["enabled"] is False
    assert flag.exists()

    on = client.put("/api/vscode-hook", json={"enabled": True}, headers=MUTATE)
    assert on.status_code == 200
    assert on.json()["enabled"] is True
    assert not flag.exists()


def test_hook_toggle_is_idempotent(make_client, hook_home):
    client = make_client(_three_providers())
    for _ in range(2):
        assert client.put(
            "/api/vscode-hook", json={"enabled": False}, headers=MUTATE
        ).json()["enabled"] is False
    for _ in range(2):
        assert client.put(
            "/api/vscode-hook", json={"enabled": True}, headers=MUTATE
        ).json()["enabled"] is True


def test_hook_reflects_manual_deletion(make_client, hook_home):
    """状态即事实：手动删掉标志文件，界面下一次查询就该说「开着」。"""
    client = make_client(_three_providers())
    client.put("/api/vscode-hook", json={"enabled": False}, headers=MUTATE)
    (hook_home / "config" / "ai-usage" / "vscode-hook.disabled").unlink()
    assert client.get("/api/vscode-hook").json()["enabled"] is True


def test_hook_put_rejects_bad_headers(make_client, hook_home):
    client = make_client(_three_providers())
    resp = client.put("/api/vscode-hook", json={"enabled": False})
    assert resp.status_code == 403
    assert not (hook_home / "config" / "ai-usage").exists()
