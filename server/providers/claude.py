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

_FORBIDDEN_MSG = (
    "HTTP 403：网络层拒绝，不是登录问题。该网络直连 api.anthropic.com 被拒，"
    "如需代理请在 config.toml 配 [providers.claude] proxy"
)

_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥"}


def _fmt_money(amount_minor: float, exponent: int, currency: str) -> str:
    """把最小货币单位格式化成人看的金额：2298/2/USD → $22.98，10000/2/USD → $100。"""
    value = amount_minor / (10**exponent)
    symbol = _CURRENCY_SYMBOLS.get(currency.upper(), currency.upper() + " ")
    text = f"{value:.{exponent}f}".rstrip("0").rstrip(".") if exponent else f"{value:.0f}"
    return f"{symbol}{text}"


def _credits_note(payload: dict) -> str | None:
    """额外用量 credit 池的金额说明，如 "$22.98 / $100"。

    优先用顶层 spend（结构最规范），退回 extra_usage 自带的字段。
    任何一步取不到就返回 None——宁可不显示，也不显示错的金额。
    """
    spend = payload.get("spend")
    if isinstance(spend, dict):
        used, limit = spend.get("used"), spend.get("limit")
        if isinstance(used, dict) and isinstance(limit, dict):
            try:
                currency = used.get("currency") or limit.get("currency") or "USD"
                return (
                    _fmt_money(used["amount_minor"], int(used.get("exponent", 2)), currency)
                    + " / "
                    + _fmt_money(limit["amount_minor"], int(limit.get("exponent", 2)), currency)
                )
            except (KeyError, TypeError, ValueError):
                pass

    extra = payload.get("extra_usage")
    if isinstance(extra, dict):
        try:
            exponent = int(extra.get("decimal_places", 2))
            currency = extra.get("currency") or "USD"
            return (
                _fmt_money(float(extra["used_credits"]), exponent, currency)
                + " / "
                + _fmt_money(float(extra["monthly_limit"]), exponent, currency)
            )
        except (KeyError, TypeError, ValueError):
            pass
    return None


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

        # 3. 请求。proxy 为 None（直连）时必须显式 trust_env=False——
        #    httpx 默认会读 HTTP_PROXY 等环境变量，不设的话"直连"名不副实。
        #    （作者所在网络裸连 Anthropic 必吃 403，故本机配置显式走代理；
        #    这不是普遍事实，默认可直连。）
        try:
            client_kwargs: dict = {"timeout": 20.0}
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            else:
                client_kwargs["proxy"] = self._proxy
                if self._proxy is None:
                    client_kwargs["trust_env"] = False
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(
                    USAGE_URL, headers={"Authorization": f"Bearer {token}"}
                )
        except httpx.TimeoutException:
            return error_usage(self.id, self.name, "error", "请求超时")
        except httpx.HTTPError as exc:
            return error_usage(self.id, self.name, "error", f"网络错误: {type(exc).__name__}")

        if resp.status_code == 401:
            return error_usage(self.id, self.name, "auth_expired", _AUTH_EXPIRED_MSG)
        if resp.status_code == 403:
            return error_usage(self.id, self.name, "error", _FORBIDDEN_MSG)
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
                    note=_credits_note(payload),
                )
            )
        return windows
