"""解析配置内容与来源；没有配置文件时使用内置默认值。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.toml"

# 安全红线：服务处理凭证数据，只允许绑回环地址
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class ConfigSource:
    path: Path | None
    origin: str


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


def load_config(path: Path | None = None, *, strict: bool = False) -> Config:
    cfg = Config()
    if path is None:
        env = os.environ.get("AI_USAGE_CONFIG")
        path = Path(env) if env else DEFAULT_CONFIG_PATH
    if not path.exists():
        if strict:
            raise FileNotFoundError(f"配置文件不存在: {path}")
        return cfg
    if strict and not path.is_file():
        raise OSError(f"配置路径不是普通文件: {path}")
    if strict and not os.access(path, os.R_OK):
        raise PermissionError(f"配置文件不可读: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        if strict:
            raise OSError(f"配置文件不可读: {path}: {exc}") from exc
        raise
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        if strict:
            raise ValueError(f"配置文件 TOML 解析失败: {path}: {exc}") from exc
        raise

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


def resolve_config(
    cli_path: str | Path | None = None,
) -> tuple[Config, ConfigSource]:
    """按 cli、环境变量、仓库默认、内置默认的顺序解析配置。"""
    if cli_path is not None:
        path = _absolute_path(cli_path)
        return load_config(path, strict=True), ConfigSource(path=path, origin="cli")

    env_path = os.environ.get("AI_USAGE_CONFIG")
    if env_path:
        path = _absolute_path(env_path)
        return load_config(path, strict=True), ConfigSource(path=path, origin="env")

    if DEFAULT_CONFIG_PATH.exists():
        path = _absolute_path(DEFAULT_CONFIG_PATH)
        return load_config(path), ConfigSource(path=path, origin="repo_default")

    return Config(), ConfigSource(path=None, origin="builtin")


def _absolute_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


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
