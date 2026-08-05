"""部署自检：一次性报告三家的可达性与配置状态。

设计要点：错误文案一律取自 provider 返回的 error 字段，doctor 只做编排与呈现，
不维护第二套措辞——否则两处会随时间漂移。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .config import ConfigSource, resolve_config
from .main import build_providers
from .providers.base import ProviderUsage, running_as_service

# status → (标记, 是否计入退出码)
_MARKS: dict[str, tuple[str, bool]] = {
    "ok": ("✓", False),
    "stale": ("~", False),  # 有数据可用，不算待办
    "error": ("✗", True),
    "auth_expired": ("✗", True),
}

_ORIGIN_LABELS = {
    "cli": "命令行 --config 指定",
    "env": "环境变量 AI_USAGE_CONFIG 指定",
    "repo_default": "仓库根目录的 config.toml",
    "builtin": "config.toml 未找到，使用内置默认值",
}


def format_report(
    source: ConfigSource, results: list[ProviderUsage]
) -> tuple[str, int]:
    """生成人类可读的报告，返回（文本, 退出码）。"""
    lines = ["ai-usage doctor", ""]

    origin_text = _ORIGIN_LABELS.get(source.origin, source.origin)
    if source.path is not None:
        origin_text = f"{origin_text}：{source.path}"
    lines.append(f"配置          {origin_text}")
    if source.origin == "builtin":
        lines.append("              模板：cp config.example.toml config.toml")

    if running_as_service():
        lines.append(
            "运行方式      systemd 服务（环境变量来自 unit，不含你 shell 里的 export）"
        )
    lines.append("")

    pending = 0
    for usage in results:
        mark, counts = _MARKS.get(usage.status, ("✗", True))
        if counts:
            pending += 1
        summary = usage.error if usage.error else "正常"
        lines.append(f"{usage.name:<12}  {mark} {summary}")
        for window in usage.windows:
            # 与面板的 fmtPct 对齐：都保留一位小数，别把浮点误差抖给用户
            lines.append(f"                · {window.label}: {window.used_pct:.1f}%")

    lines.append("")
    if pending:
        lines.append(
            f"{len(results)} 项中 {pending} 项需要处理。详见 README「Configuration」。"
        )
    else:
        lines.append("全部正常。")

    return "\n".join(lines) + "\n", (1 if pending else 0)


async def _collect(cfg) -> list[ProviderUsage]:
    return [await provider.fetch() for provider in build_providers(cfg)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ai-usage 部署自检")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="显式指定 config.toml（相对路径基于当前工作目录）",
    )
    args = parser.parse_args(argv)
    try:
        cfg, source = resolve_config(args.config)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.exit(2, f"{parser.prog}: 配置错误: {exc}\n")

    results = asyncio.run(_collect(cfg))
    text, code = format_report(source, results)
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
