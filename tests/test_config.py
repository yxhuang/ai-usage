"""Config 单测：默认值、toml 覆盖、未知键忽略。"""

from __future__ import annotations

import pytest

from server.config import load_config


def test_defaults_without_config(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8788
    assert cfg.poller.interval_seconds == 300
    assert cfg.poller.max_backoff_seconds == 1800
    assert cfg.poller.stale_after_seconds == 900
    assert cfg.providers["claude"].enabled is True
    assert (
        cfg.providers["claude"].options["credentials_path"]
        == "~/.claude/.credentials.json"
    )
    assert cfg.providers["claude"].options["proxy"] == "http://127.0.0.1:7890"
    assert cfg.providers["codex"].enabled is True
    assert cfg.providers["codex"].options == {
        "command": "codex-nowin",
        "proxy": "http://127.0.0.1:7890",
        "sessions_dir": "~/.codex/sessions",
        "app_server_timeout_seconds": 30,
        "app_server_max_failures": 3,
        "app_server_retry_after_seconds": 1800,
    }
    assert cfg.providers["kimi"].enabled is True
    assert cfg.providers["kimi"].options == {
        "api_key_env": "KIMI_API_KEY",
        "api_key_file": "~/.config/shell/secrets.sh",
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
