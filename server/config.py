"""读取仓库根 config.toml；文件不存在时用内置默认值（常态）。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.toml"

# 安全红线：服务处理凭证数据，只允许绑回环地址
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8788


@dataclass
class PollerConfig:
    interval_seconds: int = 300
    max_backoff_seconds: int = 1800
    stale_after_seconds: int = 900
    first_retry_seconds: int = 60


@dataclass
class ProviderConfig:
    enabled: bool = False
    options: dict = field(default_factory=dict)


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    poller: PollerConfig = field(default_factory=PollerConfig)
    providers: dict[str, ProviderConfig] = field(
        default_factory=lambda: {
            "claude": ProviderConfig(
                enabled=True,
                options={
                    "credentials_path": "~/.claude/.credentials.json",
                    "proxy": "http://127.0.0.1:7890",
                },
            ),
            "codex": ProviderConfig(
                enabled=True,
                options={
                    "command": "codex-nowin",
                    "proxy": "http://127.0.0.1:7890",
                    "sessions_dir": "~/.codex/sessions",
                    "app_server_timeout_seconds": 30,
                    "app_server_max_failures": 3,
                    "app_server_retry_after_seconds": 1800,
                },
            ),
            "kimi": ProviderConfig(
                enabled=True,
                options={
                    "api_key_env": "KIMI_API_KEY",
                    "api_key_file": "~/.config/shell/secrets.sh",
                },
            ),
        }
    )


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    if path is None:
        env = os.environ.get("AI_USAGE_CONFIG")
        path = Path(env) if env else DEFAULT_CONFIG_PATH
    if not path.exists():
        return cfg
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    server = data.get("server", {})
    cfg.server.host = server.get("host", cfg.server.host)
    cfg.server.port = int(server.get("port", cfg.server.port))
    if cfg.server.host not in ALLOWED_HOSTS:
        raise ValueError(
            f"server.host 只允许 127.0.0.1 / localhost / ::1，"
            f"当前配置为 {cfg.server.host!r}——该服务只绑回环地址，"
            f"禁止对外暴露，请修正 config.toml"
        )

    poller = data.get("poller", {})
    cfg.poller.interval_seconds = int(
        poller.get("interval_seconds", cfg.poller.interval_seconds)
    )
    cfg.poller.max_backoff_seconds = int(
        poller.get("max_backoff_seconds", cfg.poller.max_backoff_seconds)
    )
    cfg.poller.stale_after_seconds = int(
        poller.get("stale_after_seconds", cfg.poller.stale_after_seconds)
    )
    cfg.poller.first_retry_seconds = int(
        poller.get("first_retry_seconds", cfg.poller.first_retry_seconds)
    )

    providers = data.get("providers", {})
    for pid, pconf in providers.items():
        if not isinstance(pconf, dict):
            continue
        existing = cfg.providers.get(pid, ProviderConfig())
        options = dict(existing.options)
        options.update({k: v for k, v in pconf.items() if k != "enabled"})
        cfg.providers[pid] = ProviderConfig(
            enabled=bool(pconf.get("enabled", existing.enabled)),
            options=options,
        )
    return cfg
