"""Cache 单测：序列化往返、原子写、损坏容错、stale 标记。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server.cache import Cache
from server.providers.base import ProviderUsage, UsageWindow, parse_dt


def _usage(fetched_at: datetime | None = None, status: str = "ok") -> ProviderUsage:
    return ProviderUsage(
        id="claude",
        name="Claude",
        plan="Pro",
        windows=[
            UsageWindow(
                id="5h",
                label="5 小时窗口",
                used_pct=3.0,
                resets_at=datetime(2026, 7, 26, 11, 29, 59, tzinfo=timezone.utc),
            ),
            UsageWindow(
                id="extra_credits",
                label="额外用量 credit",
                used_pct=33.0,
                resets_at=None,
            ),
        ],
        status=status,
        error=None,
        fetched_at=fetched_at or datetime.now(timezone.utc),
    )


def test_roundtrip_lossless(tmp_path):
    cache = Cache(tmp_path / "data" / "cache.json")
    original = _usage()
    cache.set(original)

    cache2 = Cache(tmp_path / "data" / "cache.json")
    cache2.load()
    restored = cache2.get("claude")
    assert restored is not None
    assert restored.to_dict() == original.to_dict()


def test_atomic_write_no_tmp_left(tmp_path):
    path = tmp_path / "data" / "cache.json"
    cache = Cache(path)
    cache.set(_usage())
    assert path.exists()
    assert not (tmp_path / "data" / "cache.json.tmp").exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["claude"]["plan"] == "Pro"


def test_atomic_write_same_directory(tmp_path, monkeypatch):
    """os.replace 的源与目标必须同目录——跨目录 rename 不原子。"""
    import os as os_mod

    import server.cache as cache_mod

    calls: list[tuple] = []
    real_replace = os_mod.replace

    def spy(src, dst):
        calls.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(cache_mod.os, "replace", spy)
    cache = Cache(tmp_path / "data" / "cache.json")
    cache.set(_usage())
    assert calls, "落盘必须经过 os.replace"
    for src, dst in calls:
        assert Path(src).parent == Path(dst).parent


def test_persisted_json_contains_no_credentials(tmp_path):
    path = tmp_path / "cache.json"
    cache = Cache(path)
    cache.set(_usage())
    text = path.read_text(encoding="utf-8")
    assert "sk-ant" not in text
    assert "Authorization" not in text
    assert "Bearer " not in text


def test_corrupt_file_no_raise(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not valid json", encoding="utf-8")
    cache = Cache(path)
    cache.load()  # 不抛异常
    assert cache.all() == []


def test_missing_file_no_raise(tmp_path):
    cache = Cache(tmp_path / "nope" / "cache.json")
    cache.load()
    assert cache.all() == []


def test_mark_stale_if_old(tmp_path):
    cache = Cache(tmp_path / "cache.json")
    old = _usage(
        fetched_at=datetime.now(timezone.utc) - timedelta(seconds=1000)
    )
    fresh = _usage()
    fresh.id = "other"
    cache.set(old)
    cache.set(fresh)

    marked = {u.id: u for u in cache.mark_stale_if_old(900)}
    assert marked["claude"].status == "stale"
    assert marked["other"].status == "ok"
    # 内存原件不被污染
    assert cache.get("claude").status == "ok"


def test_mark_stale_skips_non_ok(tmp_path):
    cache = Cache(tmp_path / "cache.json")
    old_err = _usage(
        fetched_at=datetime.now(timezone.utc) - timedelta(seconds=1000),
        status="error",
    )
    cache.set(old_err)
    marked = cache.mark_stale_if_old(900)
    assert marked[0].status == "error"


def test_all_and_get(tmp_path):
    cache = Cache(tmp_path / "cache.json")
    assert cache.get("claude") is None
    cache.set(_usage())
    assert len(cache.all()) == 1
    assert cache.get("claude").name == "Claude"


def test_parse_dt_naive_assumes_utc():
    dt = parse_dt("2026-01-01T08:00:00")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timedelta(0)
    assert parse_dt(None) is None
    aware = parse_dt("2026-01-01T08:00:00+08:00")
    assert aware.utcoffset() == timedelta(hours=8)  # 已带时区不被改写


def test_naive_fetched_at_in_cache_no_crash(tmp_path):
    """损坏/旧版缓存里存 naive fetched_at：读回后按 UTC 补齐，stale 比较不炸。"""
    path = tmp_path / "cache.json"
    payload = _usage().to_dict()
    payload["fetched_at"] = "2020-01-01T00:00:00"  # naive
    path.write_text(
        json.dumps({"claude": payload}, ensure_ascii=False), encoding="utf-8"
    )
    cache = Cache(path)
    cache.load()
    marked = cache.mark_stale_if_old(900)  # 不抛 TypeError
    assert marked[0].status == "stale"
    assert marked[0].fetched_at.tzinfo is not None
