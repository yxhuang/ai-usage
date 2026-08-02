"""「跟随编辑器启动」开关的状态读写。

状态即事实：不另存布尔配置，直接看标志文件在不在。用户手动删掉标志文件，
界面下一次刷新就如实反映。

本模块只碰一个文件：<配置目录>/ai-usage/vscode-hook.disabled。
它由本功能创建，也只由本功能删除；除它之外不往用户系统里写任何东西。
deploy/vscode-hook.sh 和 agent-env 里的 ai-usage-panel 读的是同一个路径。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

FLAG_NAME = "vscode-hook.disabled"

# 钩子装没装，看编辑器那个被 source 的文件里有没有提到我们。
# 只是给界面一句提示，判断不准也不影响开关本身能用。
_HOOK_FILE = Path.home() / ".vscode-server" / "server-env-setup"
_HOOK_MARKERS = ("vscode-hook.sh", "ai-usage-panel")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "ai-usage"


def flag_path() -> Path:
    return config_dir() / FLAG_NAME


def _hook_installed() -> bool:
    try:
        text = _HOOK_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(m in text for m in _HOOK_MARKERS)


@dataclass
class HookStatus:
    enabled: bool
    flag_path: str
    hook_installed: bool

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "flag_path": self.flag_path,
            "hook_installed": self.hook_installed,
        }


def status() -> HookStatus:
    flag = flag_path()
    return HookStatus(
        enabled=not flag.exists(),
        flag_path=str(flag),
        hook_installed=_hook_installed(),
    )


def set_enabled(enabled: bool) -> HookStatus:
    """开 = 删掉标志文件，关 = 建一个。两个方向都幂等。"""
    flag = flag_path()
    if enabled:
        # 只删这一个由本功能创建的标志文件；不存在就是已经开着
        try:
            flag.unlink()
        except FileNotFoundError:
            pass
    else:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
    return status()
