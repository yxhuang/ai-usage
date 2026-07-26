"""内存缓存 + 原子落盘 data/cache.json（不含任何凭证）。"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .providers.base import ProviderUsage

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = REPO_ROOT / "data" / "cache.json"


class Cache:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else DEFAULT_CACHE_PATH
        self._items: dict[str, ProviderUsage] = {}

    def load(self) -> None:
        """启动时读回落盘缓存；文件缺失/损坏 → 空缓存，不抛异常。"""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for pid, d in data.items():
                self._items[pid] = ProviderUsage.from_dict(d)
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("缓存文件 %s 损坏，忽略并从空缓存启动", self._path)
            self._items = {}

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = {pid: u.to_dict() for pid, u in self._items.items()}
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._path)

    def set(self, usage: ProviderUsage) -> None:
        self._items[usage.id] = usage
        self._persist()

    def get(self, id: str) -> ProviderUsage | None:
        return self._items.get(id)

    def all(self) -> list[ProviderUsage]:
        return list(self._items.values())

    def mark_stale_if_old(self, stale_after_seconds: int) -> list[ProviderUsage]:
        """返回（可能已标记 stale 的）副本，不污染内存原件。"""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        result: list[ProviderUsage] = []
        for u in self._items.values():
            if u.status == "ok" and u.fetched_at < cutoff:
                result.append(replace(u, status="stale"))
            else:
                result.append(u)
        return result
