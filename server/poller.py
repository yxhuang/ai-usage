"""后台轮询：按 interval 拉取各 provider，失败指数退避，互不影响。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .cache import Cache
from .config import PollerConfig
from .providers.base import Provider, ProviderUsage, error_usage

logger = logging.getLogger(__name__)

TICK_SECONDS = 5


class Poller:
    def __init__(
        self, providers: list[Provider], config: PollerConfig, cache: Cache
    ) -> None:
        self._providers = {p.id: p for p in providers}
        self._config = config
        self._cache = cache
        now = datetime.now(timezone.utc)
        self._next_due: dict[str, datetime] = {p.id: now for p in providers}
        self._failures: dict[str, int] = {p.id: 0 for p in providers}

    async def _fetch_one(self, provider: Provider) -> ProviderUsage:
        try:
            usage = await provider.fetch()
        except Exception as exc:
            # 只记固定信息：异常正文可能夹带 URL / header / 凭证片段
            logger.error(
                "provider %s fetch 抛出异常（%s）", provider.id, type(exc).__name__
            )
            usage = error_usage(provider.id, provider.id, "error", "内部错误")
        try:
            self._cache.set(usage)
        except Exception as exc:
            logger.error(
                "provider %s 缓存落盘失败（%s）", provider.id, type(exc).__name__
            )
        return usage

    async def _fetch_and_schedule(self, provider: Provider) -> ProviderUsage:
        usage = await self._fetch_one(provider)
        now = datetime.now(timezone.utc)
        if usage.status == "ok":
            self._failures[provider.id] = 0
            self._next_due[provider.id] = now + timedelta(
                seconds=self._config.interval_seconds
            )
        else:
            self._failures[provider.id] += 1
            # 首次失败后快速重试，再按 2 的幂逐步拉长，封顶 max_backoff_seconds
            delay = min(
                self._config.first_retry_seconds
                * 2 ** (self._failures[provider.id] - 1),
                self._config.max_backoff_seconds,
            )
            self._next_due[provider.id] = now + timedelta(seconds=delay)
            # 只记 provider id 和 status：错误正文可能夹带 URL / 凭证片段
            logger.warning(
                "provider %s 失败（%s），%ds 后重试",
                provider.id,
                usage.status,
                delay,
            )
        return usage

    async def run_forever(self) -> None:
        while True:
            now = datetime.now(timezone.utc)
            due = [
                p
                for p in self._providers.values()
                if now >= self._next_due[p.id]
            ]
            if due:
                # 并发跑到期 provider，慢的不会拖住其他；
                # CancelledError 属 BaseException，不受 return_exceptions 影响，照常传播
                await asyncio.gather(
                    *(self._fetch_and_schedule(p) for p in due),
                    return_exceptions=True,
                )
            await asyncio.sleep(TICK_SECONDS)

    async def refresh(self, provider_id: str | None = None) -> list[ProviderUsage]:
        """立即刷新指定/全部 provider，重置其退避与 next_due。"""
        targets = (
            [self._providers[provider_id]]
            if provider_id
            else list(self._providers.values())
        )
        results: list[ProviderUsage] = []
        for provider in targets:
            usage = await self._fetch_one(provider)
            self._failures[provider.id] = 0
            self._next_due[provider.id] = datetime.now(timezone.utc) + timedelta(
                seconds=self._config.interval_seconds
            )
            results.append(usage)
        return results
