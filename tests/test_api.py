"""API 单测：TestClient 覆盖 /api/summary、/api/refresh、/。

通过 monkeypatch 注入假 provider 与 tmp_path 缓存，不碰真实凭证、不联真网。
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
        return TestClient(main_mod.create_app(cfg))

    return _make


def _three_providers():
    # 故意乱序，验证输出按 claude → codex → kimi
    return [
        FakeProvider("kimi", T0 + timedelta(seconds=20)),
        FakeProvider("codex", T0 + timedelta(seconds=10)),
        FakeProvider("claude", T0),
    ]


def test_summary_order_and_updated_at(make_client):
    client = make_client(_three_providers())
    resp = client.post("/api/refresh?provider=all")
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
    resp = client.post("/api/refresh?provider=all")
    assert resp.status_code == 200
    for p in providers:
        assert p.calls >= 1


def test_refresh_single_provider(make_client):
    providers = _three_providers()
    client = make_client(providers)
    resp = client.post("/api/refresh?provider=claude")
    assert resp.status_code == 200
    claude = next(p for p in providers if p.id == "claude")
    assert claude.calls >= 1


def test_refresh_unknown_provider_400(make_client):
    client = make_client([])
    resp = client.post("/api/refresh?provider=nope")
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
