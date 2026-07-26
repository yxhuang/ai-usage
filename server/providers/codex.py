"""Codex provider：优先查询 app-server，失败时读取 sessions 快照。

安全约束：只调用 ``account/rateLimits/read``，不读取账号身份或凭证文件。
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import ProviderUsage, UsageWindow, error_usage, parse_dt

logger = logging.getLogger(__name__)

RpcCall = Callable[[], Awaitable[dict[str, Any]]]

_FALLBACK_ERROR = (
    "无法获取 Codex 用量：app-server 不可用，且未找到可用的 sessions 限额记录"
)


class _RpcProtocolError(RuntimeError):
    """app-server 返回了错误或不符合预期的报文。"""


class CodexProvider:
    id = "codex"
    name = "Codex"

    def __init__(
        self,
        command: str = "codex-nowin",
        proxy: str | None = "http://127.0.0.1:7890",
        sessions_dir: str = "~/.codex/sessions",
        app_server_timeout_seconds: float = 30,
        app_server_max_failures: int = 3,
        app_server_retry_after_seconds: float = 1800,
        rpc_call: RpcCall | None = None,
    ) -> None:
        self._command = command
        self._proxy = proxy
        self._sessions_dir = Path(sessions_dir).expanduser()
        self._app_server_timeout_seconds = float(app_server_timeout_seconds)
        self._app_server_max_failures = max(1, int(app_server_max_failures))
        self._app_server_retry_after_seconds = max(
            0.0, float(app_server_retry_after_seconds)
        )
        self._injected_rpc_call = rpc_call
        self._app_server_failures = 0
        self._last_app_server_attempt: float | None = None

    async def fetch(self) -> ProviderUsage:
        if self._should_try_app_server():
            self._last_app_server_attempt = time.monotonic()
            try:
                rpc_result = await self._call_app_server()
                usage = self._usage_from_rate_limits(
                    rpc_result.get("rateLimits"),
                    status="ok",
                    fetched_at=datetime.now(timezone.utc),
                )
            except Exception as exc:
                self._app_server_failures += 1
                logger.warning(
                    "Codex app-server 查询失败（%s），降级读取 sessions",
                    type(exc).__name__,
                )
            else:
                self._app_server_failures = 0
                return usage

        fallback = self._read_latest_session_usage()
        if fallback is not None:
            return fallback
        return error_usage(self.id, self.name, "error", _FALLBACK_ERROR)

    def _should_try_app_server(self) -> bool:
        if self._app_server_failures < self._app_server_max_failures:
            return True
        if self._last_app_server_attempt is None:
            return True
        elapsed = time.monotonic() - self._last_app_server_attempt
        return elapsed >= self._app_server_retry_after_seconds

    async def _call_app_server(self) -> dict[str, Any]:
        call = self._injected_rpc_call or self._rpc_call
        result = call()
        if not inspect.isawaitable(result):
            raise TypeError("rpc_call must return an awaitable")
        payload = await result
        if not isinstance(payload, dict):
            raise _RpcProtocolError("rate-limit result is not an object")
        return payload

    async def _rpc_call(self) -> dict[str, Any]:
        """拉起 app-server 并完成一次 rateLimits JSON-RPC 查询。"""
        env = os.environ.copy()
        if self._proxy is not None:
            for key in ("https_proxy", "http_proxy", "HTTPS_PROXY", "HTTP_PROXY"):
                env[key] = self._proxy

        proc: asyncio.subprocess.Process | None = None
        completed = False
        protocol_failure = False
        try:
            async with asyncio.timeout(self._app_server_timeout_seconds):
                proc = await asyncio.create_subprocess_exec(
                    self._command,
                    "app-server",
                    "--stdio",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                if proc.stdin is None or proc.stdout is None:
                    raise _RpcProtocolError("app-server pipes unavailable")

                await self._write_message(
                    proc.stdin,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "clientInfo": {
                                "name": "ai-usage",
                                "version": "0.1.0",
                            }
                        },
                    },
                )
                await self._read_response(proc.stdout, response_id=1)
                await self._write_message(
                    proc.stdin,
                    {"jsonrpc": "2.0", "method": "initialized", "params": {}},
                )
                await self._write_message(
                    proc.stdin,
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "account/rateLimits/read",
                        "params": {},
                    },
                )
                result = await self._read_response(proc.stdout, response_id=2)
                completed = True
                return result
        except _RpcProtocolError:
            protocol_failure = True
            raise
        finally:
            if proc is not None:
                await self._reap_process(proc, graceful=completed)
                if protocol_failure:
                    stderr_preview = await self._stderr_preview(proc)
                    if stderr_preview:
                        logger.warning(
                            "Codex app-server 报文异常，stderr 摘要: %s",
                            stderr_preview,
                        )

    @staticmethod
    async def _write_message(
        writer: asyncio.StreamWriter, message: dict[str, Any]
    ) -> None:
        writer.write(
            json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode()
            + b"\n"
        )
        await writer.drain()

    @staticmethod
    async def _read_response(
        reader: asyncio.StreamReader, response_id: int
    ) -> dict[str, Any]:
        while True:
            line = await reader.readline()
            if not line:
                raise _RpcProtocolError("app-server stdout closed")
            try:
                message = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise _RpcProtocolError("invalid JSON-RPC message") from exc
            if not isinstance(message, dict):
                raise _RpcProtocolError("JSON-RPC message is not an object")
            if message.get("id") != response_id:
                continue
            if "error" in message:
                raise _RpcProtocolError("JSON-RPC returned an error")
            result = message.get("result")
            if not isinstance(result, dict):
                raise _RpcProtocolError("JSON-RPC result is not an object")
            return result

    @staticmethod
    async def _reap_process(
        proc: asyncio.subprocess.Process, *, graceful: bool
    ) -> None:
        if proc.stdin is not None and not proc.stdin.is_closing():
            proc.stdin.close()
            with contextlib.suppress(Exception):
                await proc.stdin.wait_closed()

        if proc.returncode is None:
            if graceful:
                proc.terminate()
            else:
                proc.kill()

        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()

    @staticmethod
    async def _stderr_preview(proc: asyncio.subprocess.Process) -> str:
        if proc.stderr is None:
            return ""
        with contextlib.suppress(Exception):
            raw = await proc.stderr.read(500)
            return raw.decode("utf-8", errors="replace").strip()
        return ""

    def _read_latest_session_usage(self) -> ProviderUsage | None:
        files_with_mtime: list[tuple[float, Path]] = []
        for path in self._sessions_dir.glob("**/rollout-*.jsonl"):
            try:
                files_with_mtime.append((path.stat().st_mtime, path))
            except OSError:
                continue

        files_with_mtime.sort(key=lambda item: item[0], reverse=True)
        for _, path in files_with_mtime:
            usage = self._last_valid_session_usage(path)
            if usage is not None:
                return usage
        return None

    def _last_valid_session_usage(self, path: Path) -> ProviderUsage | None:
        latest: ProviderUsage | None = None
        try:
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not isinstance(item, dict):
                        continue
                    payload = item.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    rate_limits = payload.get("rate_limits")
                    if not isinstance(rate_limits, dict):
                        continue
                    try:
                        fetched_at = parse_dt(item.get("timestamp"))
                        if fetched_at is None:
                            raise ValueError("session timestamp missing")
                        latest = self._usage_from_rate_limits(
                            rate_limits,
                            status="stale",
                            fetched_at=fetched_at,
                        )
                    except (TypeError, ValueError):
                        continue
        except OSError:
            return None
        return latest

    def _usage_from_rate_limits(
        self,
        rate_limits: Any,
        *,
        status: str,
        fetched_at: datetime,
    ) -> ProviderUsage:
        if not isinstance(rate_limits, dict):
            raise ValueError("rate limits missing")

        windows: list[tuple[int, UsageWindow]] = []
        for key in ("primary", "secondary"):
            raw_window = rate_limits.get(key)
            if raw_window is None:
                continue
            if not isinstance(raw_window, dict):
                raise ValueError("rate-limit window is not an object")
            duration = self._value(
                raw_window, "windowDurationMins", "window_minutes"
            )
            used_pct = self._value(raw_window, "usedPercent", "used_percent")
            if duration is None or used_pct is None:
                raise ValueError("rate-limit window fields missing")
            duration_mins = int(duration)
            window_id, label = self._window_identity(duration_mins)
            reset_value = self._value(raw_window, "resetsAt", "resets_at")
            resets_at = (
                datetime.fromtimestamp(float(reset_value), tz=timezone.utc)
                if reset_value is not None
                else None
            )
            windows.append(
                (
                    duration_mins,
                    UsageWindow(
                        id=window_id,
                        label=label,
                        used_pct=float(used_pct),
                        resets_at=resets_at,
                    ),
                )
            )

        if not windows:
            raise ValueError("no rate-limit windows")
        windows.sort(key=lambda item: item[0])

        plan_raw = self._value(rate_limits, "planType", "plan_type")
        plan = plan_raw.capitalize() if isinstance(plan_raw, str) else None
        return ProviderUsage(
            id=self.id,
            name=self.name,
            plan=plan,
            windows=[window for _, window in windows],
            status=status,
            error=None,
            fetched_at=fetched_at,
        )

    @staticmethod
    def _value(data: dict[str, Any], camel: str, snake: str) -> Any:
        if camel in data:
            return data[camel]
        return data.get(snake)

    @staticmethod
    def _window_identity(duration_mins: int) -> tuple[str, str]:
        if duration_mins == 300:
            return "5h", "5 小时窗口"
        if duration_mins == 10080:
            return "week", "周额度"
        if duration_mins % 60 == 0:
            hours = duration_mins // 60
            return f"{hours}h", f"{hours} 小时窗口"
        return f"{duration_mins}m", f"{duration_mins} 分钟窗口"
