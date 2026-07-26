"""Provider 统一契约（spec §4.0）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


def parse_dt(value: str | None) -> datetime | None:
    """解析 ISO8601；naive 一律按 UTC 补齐（防御性兜底，避免缓存比较时炸）。"""
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None or dt.utcoffset() is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class UsageWindow:
    id: str  # "5h" | "week" | "week_opus" | "extra_credits" | 透传的未知 kind
    label: str  # 中文显示名
    used_pct: float  # 0-100
    resets_at: datetime | None  # tz-aware
    # 百分比之外值得一并显示的绝对量，已格式化好（如按金额计费的 "$22.98 / $100"）。
    # 没有就留 None——不是每种额度都有有意义的绝对量。
    note: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "used_pct": self.used_pct,
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UsageWindow":
        return cls(
            id=d["id"],
            label=d["label"],
            used_pct=float(d["used_pct"]),
            resets_at=parse_dt(d.get("resets_at")),
            note=d.get("note"),
        )


@dataclass
class ProviderUsage:
    id: str
    name: str
    plan: str | None
    windows: list[UsageWindow]
    status: str  # "ok" | "stale" | "auth_expired" | "error"
    error: str | None
    fetched_at: datetime

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "plan": self.plan,
            "windows": [w.to_dict() for w in self.windows],
            "status": self.status,
            "error": self.error,
            "fetched_at": self.fetched_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderUsage":
        return cls(
            id=d["id"],
            name=d["name"],
            plan=d.get("plan"),
            windows=[UsageWindow.from_dict(w) for w in d.get("windows", [])],
            status=d.get("status", "ok"),
            error=d.get("error"),
            fetched_at=parse_dt(d["fetched_at"]),
        )


class Provider(Protocol):
    id: str

    async def fetch(self) -> ProviderUsage: ...


def error_usage(id: str, name: str, status: str, error: str) -> ProviderUsage:
    """各 provider 出错时复用的兜底 ProviderUsage。"""
    return ProviderUsage(
        id=id,
        name=name,
        plan=None,
        windows=[],
        status=status,
        error=error,
        fetched_at=datetime.now(timezone.utc),
    )
