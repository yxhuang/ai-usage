"""doctor 子命令单测：只测编排与呈现，provider 一律用假数据注入。"""

from __future__ import annotations

from datetime import datetime, timezone

from server.config import ConfigSource
from server.doctor import format_report
from server.providers.base import ProviderUsage, UsageWindow


def _usage(pid: str, name: str, status: str, error: str | None = None) -> ProviderUsage:
    return ProviderUsage(
        id=pid,
        name=name,
        plan="TestPlan",
        windows=[UsageWindow(id="week", label="周额度", used_pct=25.0, resets_at=None)],
        status=status,
        error=error,
        fetched_at=datetime.now(timezone.utc),
    )


def test_all_ok_exits_zero():
    source = ConfigSource(path=None, origin="builtin")
    results = [
        _usage("claude", "Claude", "ok"),
        _usage("codex", "Codex", "ok"),
        _usage("kimi", "Kimi", "ok"),
    ]
    text, code = format_report(source, results)
    assert code == 0
    assert "✓" in text


def test_error_exits_one_and_shows_provider_message():
    """错误文案直接取自 provider，doctor 不维护第二套措辞。"""
    source = ConfigSource(path=None, origin="builtin")
    results = [
        _usage("claude", "Claude", "error", "HTTP 403：网络层拒绝，不是登录问题"),
        _usage("kimi", "Kimi", "error", "未配置 Kimi API key"),
    ]
    text, code = format_report(source, results)
    assert code == 1
    assert "403" in text
    assert "未配置 Kimi API key" in text


def test_stale_is_warning_not_failure():
    """有数据可用就不算待办，stale 不计入退出码。"""
    source = ConfigSource(path=None, origin="builtin")
    results = [_usage("codex", "Codex", "stale", "app-server 不可用")]
    text, code = format_report(source, results)
    assert code == 0
    assert "~" in text


def test_report_mentions_config_template_when_builtin():
    source = ConfigSource(path=None, origin="builtin")
    text, _ = format_report(source, [_usage("claude", "Claude", "ok")])
    assert "config.example.toml" in text


def test_report_never_leaks_credentials():
    """凭证保密约束：报告里不得出现任何 key 片段。"""
    source = ConfigSource(path=None, origin="builtin")
    results = [_usage("kimi", "Kimi", "ok")]
    text, _ = format_report(source, results)
    assert "sk-" not in text
