"""Poller 单测：指数退避、成功清零、异常隔离、并发、refresh、日志脱敏。"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import pytest

from server.cache import Cache
from server.config import PollerConfig
from server.poller import Poller
from server.providers.base import ProviderUsage, UsageWindow, error_usage


def _ok_usage(pid: str) -> ProviderUsage:
    return ProviderUsage(
        id=pid,
        name=pid,
        plan=None,
        windows=[
            UsageWindow(id="5h", label="5 小时窗口", used_pct=1.0, resets_at=None)
        ],
        status="ok",
        error=None,
        fetched_at=datetime.now(timezone.utc),
    )


class FakeProvider:
    """behavior: "ok" | "error" | "raise" | async callable"""

    def __init__(self, pid: str, behavior="ok") -> None:
        self.id = pid
        self.behavior = behavior
        self.calls = 0

    async def fetch(self) -> ProviderUsage:
        self.calls += 1
        if callable(self.behavior):
            return await self.behavior(self)
        if self.behavior == "ok":
            return _ok_usage(self.id)
        if self.behavior == "error":
            return error_usage(self.id, self.id, "error", "模拟失败")
        raise RuntimeError("boom")


def _poller(
    tmp_path, providers, interval: int = 10, max_backoff: int = 50, cache=None
) -> Poller:
    cfg = PollerConfig(
        interval_seconds=interval,
        max_backoff_seconds=max_backoff,
        stale_after_seconds=900,
    )
    return Poller(
        providers, cfg, cache if cache is not None else Cache(tmp_path / "cache.json")
    )


async def test_backoff_exponential_and_capped(tmp_path):
    p = FakeProvider("a", "error")
    poller = _poller(tmp_path, [p], interval=10, max_backoff=50)
    for want in (20, 40, 50, 50):  # 10*2、10*4、封顶 50、保持 50
        before = datetime.now(timezone.utc)
        await poller._fetch_and_schedule(p)
        delay = (poller._next_due["a"] - before).total_seconds()
        assert delay == pytest.approx(want, abs=2)


async def test_success_resets_backoff(tmp_path):
    p = FakeProvider("a", "error")
    poller = _poller(tmp_path, [p], interval=10, max_backoff=50)
    await poller._fetch_and_schedule(p)
    assert poller._failures["a"] == 1

    p.behavior = "ok"
    before = datetime.now(timezone.utc)
    await poller._fetch_and_schedule(p)
    assert poller._failures["a"] == 0
    delay = (poller._next_due["a"] - before).total_seconds()
    assert delay == pytest.approx(10, abs=2)


async def test_refresh_resets_backoff(tmp_path):
    p = FakeProvider("a", "error")
    poller = _poller(tmp_path, [p], interval=10, max_backoff=50)
    await poller._fetch_and_schedule(p)
    assert poller._failures["a"] == 1

    p.behavior = "ok"
    before = datetime.now(timezone.utc)
    results = await poller.refresh("a")
    assert results[0].status == "ok"
    assert poller._failures["a"] == 0
    delay = (poller._next_due["a"] - before).total_seconds()
    assert delay == pytest.approx(10, abs=2)


async def test_exception_isolated_and_log_sanitized(tmp_path, caplog):
    secret = "sk-ant-secret"
    url = "https://api.anthropic.com/x?token=abc"

    async def boom(self):
        raise RuntimeError(f"failed with {secret} at {url}")

    bad = FakeProvider("bad", boom)
    good = FakeProvider("good", "ok")
    poller = _poller(tmp_path, [bad, good])

    with caplog.at_level(logging.ERROR, logger="server.poller"):
        results = await poller.refresh(None)

    # 抛异常不影响其他 provider
    by_id = {u.id: u for u in results}
    assert by_id["bad"].status == "error"
    assert by_id["good"].status == "ok"

    # 日志：含 provider id 和异常类名，不含 token / URL / 异常正文
    assert "bad" in caplog.text
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text
    assert url not in caplog.text
    assert "failed with" not in caplog.text
    for record in caplog.records:
        assert record.exc_info is None  # 不带 traceback


async def test_concurrent_fetch_not_serial(tmp_path):
    async def slow(self):
        await asyncio.sleep(0.3)
        return _ok_usage(self.id)

    p1 = FakeProvider("a", slow)
    p2 = FakeProvider("b", slow)
    poller = _poller(tmp_path, [p1, p2])

    start = time.monotonic()
    task = asyncio.create_task(poller.run_forever())
    try:
        for _ in range(200):
            if p1.calls >= 1 and p2.calls >= 1:
                break
            await asyncio.sleep(0.01)
        elapsed = time.monotonic() - start
        assert p1.calls >= 1 and p2.calls >= 1
        # 串行需 ~0.6s，并发 ~0.3s
        assert elapsed < 0.5
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_cache_set_failure_not_fatal(tmp_path):
    class BrokenCache(Cache):
        def set(self, usage):
            raise RuntimeError("disk full")

    p1 = FakeProvider("a", "ok")
    p2 = FakeProvider("b", "ok")
    poller = _poller(tmp_path, [p1, p2], cache=BrokenCache(tmp_path / "c.json"))

    # 后台循环不终止，两个 provider 照常抓取
    task = asyncio.create_task(poller.run_forever())
    try:
        for _ in range(200):
            if p1.calls >= 1 and p2.calls >= 1:
                break
            await asyncio.sleep(0.01)
        assert p1.calls >= 1 and p2.calls >= 1
        assert not task.done()

        # refresh 也正常返回（cache.set 抛异常被吞掉，只记日志）
        results = await poller.refresh(None)
        assert [u.status for u in results] == ["ok", "ok"]
        assert p1.calls >= 2 and p2.calls >= 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
