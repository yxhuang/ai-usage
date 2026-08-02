"""Config 单测：默认值、toml 覆盖、未知键忽略、proxy 归一化。"""

from __future__ import annotations

import pytest

from server.config import load_config
from server.main import build_providers


def test_defaults_without_config(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8788
    assert cfg.poller.interval_seconds == 300
    assert cfg.poller.max_backoff_seconds == 1800
    assert cfg.poller.stale_after_seconds == 900
    # 内置默认必须是通用值：无代理（直连）、裸 codex 命令、无私有密钥文件
    assert cfg.providers["claude"].enabled is True
    assert cfg.providers["claude"].options == {
        "credentials_path": "~/.claude/.credentials.json",
    }
    assert cfg.providers["codex"].enabled is True
    assert cfg.providers["codex"].options == {
        "command": "codex",
        "sessions_dir": "~/.codex/sessions",
        "app_server_timeout_seconds": 30,
        "app_server_max_failures": 3,
        "app_server_retry_after_seconds": 1800,
    }
    assert cfg.providers["kimi"].enabled is True
    assert cfg.providers["kimi"].options == {
        "api_key_env": "KIMI_API_KEY",
        "api_key_file": None,
    }


def test_overrides_from_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[server]
port = 9999

[poller]
interval_seconds = 60

[providers.claude]
proxy = "http://127.0.0.1:1080"

[providers.codex]
enabled = true
""",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.server.port == 9999
    assert cfg.server.host == "127.0.0.1"  # 未覆盖项保持默认
    assert cfg.poller.interval_seconds == 60
    assert cfg.poller.stale_after_seconds == 900
    assert cfg.providers["claude"].options["proxy"] == "http://127.0.0.1:1080"
    # 未覆盖的 claude 选项保持默认
    assert (
        cfg.providers["claude"].options["credentials_path"]
        == "~/.claude/.credentials.json"
    )
    assert cfg.providers["codex"].enabled is True


def test_unknown_keys_ignored(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[server]
port = 8788
nonsense_key = "whatever"

[mystery_section]
foo = 1

[providers.future_provider]
enabled = true
some_option = "x"
""",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.server.port == 8788
    assert cfg.providers["future_provider"].enabled is True
    assert cfg.providers["future_provider"].options["some_option"] == "x"


def test_public_host_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[server]\nhost = "0.0.0.0"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="只允许"):
        load_config(path)


def test_loopback_hosts_accepted(tmp_path):
    for host in ("127.0.0.1", "localhost", "::1"):
        path = tmp_path / f"config-{host.replace(':', '_').replace('.', '_')}.toml"
        path.write_text(f'[server]\nhost = "{host}"\n', encoding="utf-8")
        assert load_config(path).server.host == host


def _write(tmp_path, text: str):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_proxy_empty_and_whitespace_normalize_to_none(tmp_path):
    for raw in ('""', '"   "'):
        cfg = load_config(
            _write(tmp_path, f'[providers.claude]\nproxy = {raw}\n')
        )
        assert cfg.providers["claude"].options["proxy"] is None


def test_proxy_url_preserved_and_stripped(tmp_path):
    cfg = load_config(
        _write(tmp_path, '[providers.claude]\nproxy = " http://127.0.0.1:1080 "\n')
    )
    assert cfg.providers["claude"].options["proxy"] == "http://127.0.0.1:1080"


def test_proxy_invalid_type_is_clear_config_error(tmp_path):
    path = _write(tmp_path, "[providers.claude]\nproxy = 7890\n")
    with pytest.raises(ValueError, match="providers.claude.proxy 必须是字符串"):
        load_config(path)


def test_proxy_missing_stays_direct(tmp_path):
    cfg = load_config(_write(tmp_path, "[providers.claude]\nenabled = true\n"))
    assert "proxy" not in cfg.providers["claude"].options


def test_build_providers_passes_normalized_values(tmp_path):
    cfg = load_config(
        _write(
            tmp_path,
            '[providers.claude]\nproxy = " "\n'
            '[providers.codex]\nproxy = "http://127.0.0.1:1080"\n'
            '[providers.kimi]\nproxy = "http://127.0.0.1:1081"\n',
        )
    )
    claude, codex, kimi = build_providers(cfg)
    assert claude._proxy is None
    assert codex._proxy == "http://127.0.0.1:1080"
    assert kimi._proxy == "http://127.0.0.1:1081"


def test_build_providers_default_config_is_direct(tmp_path):
    """默认配置（无 config.toml）构造三家 provider，网络层必须全是直连。

    进程环境里的代理变量已由 conftest 清空；此处断言配置侧不引入任何代理。
    """
    cfg = load_config(tmp_path / "nonexistent.toml")
    claude, codex, kimi = build_providers(cfg)
    assert claude._proxy is None
    assert codex._proxy is None
    assert codex._command == "codex"
    assert kimi._proxy is None
    assert kimi._api_key_file is None


async def test_build_providers_proxy_reaches_network_layer(tmp_path, monkeypatch):
    """执行层断言：config 里的 proxy 必须真的传到 AsyncClient 构造参数。

    只查 provider._proxy 捕获不了「参数名写错」「没真正传给 httpx」这类回归。
    """
    import json
    from datetime import datetime, timedelta, timezone

    import httpx
    from tests.test_claude_provider import FIXTURE

    cred = tmp_path / "credentials.json"
    expires = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000)
    cred.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "fake", "expiresAt": expires}}),
        encoding="utf-8",
    )
    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            return httpx.Response(200, text=FIXTURE)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    cfg = load_config(
        _write(
            tmp_path,
            f'[providers.claude]\ncredentials_path = "{cred}"\n'
            'proxy = "http://127.0.0.1:1080"\n',
        )
    )
    (claude,) = [p for p in build_providers(cfg) if p.id == "claude"]
    usage = await claude.fetch()
    assert usage.status == "ok"
    assert captured.get("proxy") == "http://127.0.0.1:1080"
