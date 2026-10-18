"""Provider 统一契约（spec §4.0）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
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
class ResetCredit:
    # date：只知道过期日（手动登记，如网页「Expires Oct 23」），前端只显示日期
    expires_at: datetime | date | None
    title: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"expires_at": self.expires_at.isoformat() if self.expires_at else None}
        if not isinstance(self.expires_at, datetime) and self.expires_at is not None:
            d["all_day"] = True
        if self.title:
            d["title"] = self.title
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ResetCredit":
        raw = d.get("expires_at")
        expires = date.fromisoformat(raw) if raw and d.get("all_day") else parse_dt(raw)
        return cls(expires_at=expires, title=d.get("title"))


@dataclass
class ResetCredits:
    available_count: int
    credits: list[ResetCredit] | None  # None：只知道数量；[]：没有卡片明细

    def to_dict(self) -> dict:
        return {
            "available_count": self.available_count,
            "credits": (
                [c.to_dict() for c in self.credits]
                if self.credits is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ResetCredits":
        credits = d.get("credits")
        return cls(
            available_count=d["available_count"],
            credits=(
                [ResetCredit.from_dict(c) for c in credits]
                if credits is not None else None
            ),
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
    reset_credits: ResetCredits | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "plan": self.plan,
            "windows": [w.to_dict() for w in self.windows],
            "status": self.status,
            "error": self.error,
            "fetched_at": self.fetched_at.isoformat(),
            "reset_credits": self.reset_credits.to_dict() if self.reset_credits else None,
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
            reset_credits=(
                ResetCredits.from_dict(d["reset_credits"])
                if d.get("reset_credits") is not None else None
            ),
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


def running_as_service() -> bool:
    """systemd 拉起的进程会带 INVOCATION_ID；据此判断环境变量那条路是否可用。"""
    return bool(os.environ.get("INVOCATION_ID"))
