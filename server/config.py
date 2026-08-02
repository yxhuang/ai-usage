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
    # 内置默认必须是通用值：任何私人环境（本机代理端口、私有包裹脚本、
    # 个人密钥文件路径）只能出现在不入库的 config.toml 里。
    # proxy 语义：缺失 / 空串 / 纯空白 = 直连（且忽略环境变量里的代理）；
    # 非空 URL = 使用该代理。
    providers: dict[str, ProviderConfig] = field(
        default_factory=lambda: {
            "claude": ProviderConfig(
                enabled=True,
                options={
                    "credentials_path": "~/.claude/.credentials.json",
                },
            ),
            "codex": ProviderConfig(
                enabled=True,
                options={
                    "command": "codex",
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
                    "api_key_file": None,
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
    for pid, pconf in cfg.providers.items():
        if "proxy" in pconf.options:
            pconf.options["proxy"] = _normalize_proxy(pid, pconf.options["proxy"])
    return cfg


def _normalize_proxy(pid: str, value: object) -> str | None:
    """proxy 归一化：缺失/空串/纯空白 → None（直连）；非空 URL 原样保留。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"providers.{pid}.proxy 必须是字符串 URL 或不写（直连），"
            f"当前配置为 {value!r}，请修正 config.toml"
        )
    stripped = value.strip()
    return stripped or None
