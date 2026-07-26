"""Claude provider：读 claude CLI 的 OAuth 凭证，调 /api/oauth/usage。

安全约束：token 只在内存，绝不写日志/异常消息。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .base import ProviderUsage, UsageWindow, error_usage, parse_dt

logger = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

_KIND_MAP: dict[str, tuple[str, str]] = {
    "session": ("5h", "5 小时窗口"),
    "weekly_all": ("week", "周额度"),
    "weekly_opus": ("week_opus", "周 Opus 额度"),
}

_AUTH_EXPIRED_MSG = "OAuth token 已过期——随便用一次 claude CLI 即自动续期"


class ClaudeProvider:
    id = "claude"
    name = "Claude"

    def __init__(
        self,
        credentials_path: str,
        proxy: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._credentials_path = Path(credentials_path).expanduser()
        self._proxy = proxy
        self._transport = transport  # 测试注入用；生产为 None

    def _read_credentials(self) -> tuple[str, int | None, str | None]:
        """返回 (access_token, expires_at_ms, plan)。失败抛异常。"""
        data = json.loads(self._credentials_path.read_text(encoding="utf-8"))
        oauth = data.get("claudeAiOauth") or {}
        token = oauth.get("accessToken")
        if not token:
            raise ValueError("missing accessToken")
        expires_at = oauth.get("expiresAt")
        plan_raw = oauth.get("subscriptionType")
        plan = plan_raw.capitalize() if isinstance(plan_raw, str) else None
        return token, expires_at, plan

    async def fetch(self) -> ProviderUsage:
        # 1. 读凭证
        try:
            token, expires_at_ms, plan = self._read_credentials()
        except FileNotFoundError:
            return error_usage(
                self.id, self.name, "error", "未找到 Claude 凭证文件，请先登录 claude CLI"
            )
        except Exception:
            logger.warning("Claude 凭证文件解析失败（内容不入日志）")
            return error_usage(
                self.id, self.name, "error", "Claude 凭证文件解析失败，请重新登录 claude CLI"
            )

        # 2. 本地过期检查，不发请求
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if isinstance(expires_at_ms, int) and expires_at_ms <= now_ms:
            return error_usage(self.id, self.name, "auth_expired", _AUTH_EXPIRED_MSG)

        # 3. 请求（显式走代理；裸连必吃 403）
        try:
            client_kwargs: dict = {"timeout": 20.0}
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            else:
                client_kwargs["proxy"] = self._proxy
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(
                    USAGE_URL, headers={"Authorization": f"Bearer {token}"}
                )
        except httpx.TimeoutException:
            return error_usage(self.id, self.name, "error", "请求超时")
        except httpx.HTTPError as exc:
            return error_usage(self.id, self.name, "error", f"网络错误: {type(exc).__name__}")

        if resp.status_code in (401, 403):
            return error_usage(self.id, self.name, "auth_expired", _AUTH_EXPIRED_MSG)
        if resp.status_code < 200 or resp.status_code >= 300:
            return error_usage(
                self.id, self.name, "error", f"HTTP {resp.status_code}"
            )

        # 4. 解析
        try:
            payload = resp.json()
            windows = self._parse_windows(payload)
        except Exception as exc:
            # 只记固定信息：异常正文可能夹带报文内容
            logger.warning("Claude usage 报文解析失败（%s）", type(exc).__name__)
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
            # limits 是主数据源，缺失/类型不对一律按解析失败处理，
            # 不能静默返回"健康但没有额度"
            raise ValueError("limits missing or not a list")
        windows: list[UsageWindow] = []
        for item in limits:
            kind = item.get("kind", "")
            wid, label = _KIND_MAP.get(kind, (kind, kind))
            windows.append(
                UsageWindow(
                    id=wid,
                    label=label,
                    used_pct=float(item.get("percent") or 0.0),
                    resets_at=parse_dt(item.get("resets_at")),
                )
            )

        # 顺序：5h → week → 其它 weekly_* → 其它 → extra_credits
        order = {"5h": 0, "week": 1, "extra_credits": 90}
        windows.sort(
            key=lambda w: (order.get(w.id, 10 if w.id.startswith("week") else 20))
        )

        extra = payload.get("extra_usage")
        if extra and extra.get("is_enabled"):
            windows.append(
                UsageWindow(
                    id="extra_credits",
                    label="额外用量 credit",
                    used_pct=float(extra.get("utilization") or 0.0),
                    resets_at=None,
                )
            )
        return windows
