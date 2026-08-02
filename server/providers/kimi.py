"""Kimi provider：用 sk-kimi-* API key 请求 api.kimi.com。

安全约束：key 只在内存，绝不写日志/异常消息。默认直连：不传 proxy 且
trust_env=False，不读环境变量里的代理设置（HTTP_PROXY/HTTPS_PROXY 等）；
企业网络用户可在 config.toml 里为 Kimi 显式配置 proxy。注意这不等于流量
一定没有经过本机代理软件：TUN 模式的代理在 IP 层接管路由，本模块无法感知。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .base import ProviderUsage, UsageWindow, error_usage, parse_dt

logger = logging.getLogger(__name__)

USAGE_URL = "https://api.kimi.com/coding/v1/usages"

_NO_KEY_MSG = (
    "未配置 Kimi API key（config.toml 的 api_key / 环境变量 {env} / 配置的密钥文件 三者任一）"
)
_AUTH_EXPIRED_MSG = "Kimi API key 无效或已过期，请更新 key"


class KimiProvider:
    id = "kimi"
    name = "Kimi"

    def __init__(
        self,
        api_key: str | None = None,
        api_key_env: str = "KIMI_API_KEY",
        api_key_file: str | None = None,
        proxy: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._api_key_file = Path(api_key_file).expanduser() if api_key_file else None
        self._proxy = proxy
        self._transport = transport  # 测试注入用；生产为 None

    def _resolve_api_key(self) -> str | None:
        """按优先级取 key：config → 环境变量 → 密钥文件正则提取。"""
        if self._api_key:
            return self._api_key
        env_value = os.environ.get(self._api_key_env, "").strip()
        if env_value:
            return env_value
        return self._key_from_file()

    def _key_from_file(self) -> str | None:
        """从 shell 密钥文件里正则提取变量值；只做正则匹配，禁止 source/exec。"""
        if self._api_key_file is None:
            return None
        try:
            text = self._api_key_file.read_text(encoding="utf-8")
        except OSError:
            return None
        # 形如 export KIMI_API_KEY="sk-..." / KIMI_API_KEY='sk-...' / KIMI_API_KEY=sk-...
        pattern = re.compile(
            r"^\s*(?:export\s+)?"
            + re.escape(self._api_key_env)
            + r"\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s#]+))",
            re.MULTILINE,
        )
        match = pattern.search(text)
        if not match:
            return None
        value = next((g for g in match.groups() if g is not None), "").strip()
        return value or None

    async def fetch(self) -> ProviderUsage:
        # 1. 解析 key；三条路都没有直接报错，不发请求
        key = self._resolve_api_key()
        if not key:
            return error_usage(
                self.id, self.name, "error", _NO_KEY_MSG.format(env=self._api_key_env)
            )

        # 2. 请求：默认直连——不传 proxy 且 trust_env=False，只保证不读环境
        #    变量里的代理设置；显式配置了 proxy 时使用它（trust_env 仍保持
        #    False，行为只由配置决定）。若本机代理软件开了 TUN 模式
        #    （IP 层接管路由），流量仍可能被接管，本模块对此无法感知
        client_kwargs: dict = {"timeout": 20.0, "trust_env": False}
        if self._proxy is not None:
            client_kwargs["proxy"] = self._proxy
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(
                    USAGE_URL,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Accept": "application/json",
                    },
                )
        except httpx.TimeoutException:
            return error_usage(self.id, self.name, "error", "请求超时")
        except httpx.HTTPError as exc:
            # 只带异常类型名：异常正文可能夹带 URL/key
            return error_usage(
                self.id, self.name, "error", f"网络错误: {type(exc).__name__}"
            )

        if resp.status_code in (401, 403):
            return error_usage(self.id, self.name, "auth_expired", _AUTH_EXPIRED_MSG)
        if resp.status_code < 200 or resp.status_code >= 300:
            return error_usage(self.id, self.name, "error", f"HTTP {resp.status_code}")

        # 3. 解析（user 字段是账号身份信息，一律不取）
        try:
            payload = resp.json()
            windows = self._parse_windows(payload)
            plan = self._parse_plan(payload)
        except Exception as exc:
            logger.warning("Kimi usage 报文解析失败（%s）", type(exc).__name__)
            return error_usage(self.id, self.name, "error", "响应报文解析失败")

        return ProviderUsage(
            id=self.id,
            name=self.name,
            plan=plan,
            windows=windows,
            status="ok",
            error=None,
            fetched_at=datetime.now(timezone.utc),
        )

    def _parse_windows(self, payload: dict) -> list[UsageWindow]:
        limits = payload.get("limits")
        if not isinstance(limits, list):
            # limits 是短窗口主数据源，缺失/类型不对一律按解析失败处理
            raise ValueError("limits missing or not a list")
        short: list[tuple[int, UsageWindow]] = []
        for item in limits:
            if not isinstance(item, dict):
                raise ValueError("limits entry is not an object")
            window = item.get("window")
            detail = item.get("detail")
            if not isinstance(window, dict) or not isinstance(detail, dict):
                raise ValueError("limits entry fields missing")
            minutes = self._window_minutes(window)
            if minutes is None:
                continue  # 无法识别的时间单位，跳过
            wid, label = self._window_identity(minutes)
            short.append(
                (
                    minutes,
                    UsageWindow(
                        id=wid,
                        label=label,
                        used_pct=self._used_pct(detail),
                        resets_at=parse_dt(detail.get("resetTime")),
                    ),
                )
            )
        short.sort(key=lambda entry: entry[0])  # 短窗口在前

        windows = [w for _, w in short]
        usage = payload.get("usage")
        if isinstance(usage, dict):
            windows.append(
                UsageWindow(
                    id="week",
                    label="周额度",
                    used_pct=self._used_pct(usage),
                    resets_at=parse_dt(usage.get("resetTime")),
                )
            )
        return windows

    @staticmethod
    def _window_minutes(window: dict) -> int | None:
        duration = window.get("duration")
        unit = str(window.get("timeUnit") or "")
        if not isinstance(duration, (int, float)):
            return None
        if "MINUTE" in unit:
            return int(duration)
        if "HOUR" in unit:
            return int(duration) * 60
        if "DAY" in unit:
            return int(duration) * 60 * 24
        return None

    @staticmethod
    def _window_identity(minutes: int) -> tuple[str, str]:
        if minutes == 300:
            return "5h", "5 小时窗口"
        if minutes % 60 == 0:
            hours = minutes // 60
            return f"{hours}h", f"{hours} 小时窗口"
        return f"{minutes}m", f"{minutes} 分钟窗口"

    @staticmethod
    def _used_pct(detail: dict) -> float:
        """报文的 limit/used 是字符串；limit 为 0 或缺失时置 0.0，不除零。"""
        try:
            limit = float(detail.get("limit") or 0)
            used = float(detail.get("used") or 0)
        except (TypeError, ValueError):
            return 0.0
        if limit <= 0:
            return 0.0
        return used / limit * 100

    @staticmethod
    def _parse_plan(payload: dict) -> str | None:
        raw = payload.get("subType")
        if not isinstance(raw, str) or not raw:
            return None
        if raw.startswith("TYPE_"):
            raw = raw[len("TYPE_"):]
        return raw.capitalize() or None
