# 部署诊断改造 实施计划

> **For agentic workers:** 按任务顺序逐个执行，每个任务自带测试与提交。步骤用 `- [ ]` 勾选跟踪。

**Goal:** 让任一机器部署本项目后，安装脚本结束时就能看到三家的通断与补救办法，且面板里每条错误信息都指向真因。

**Architecture:** 新增 `server/doctor.py` 作为独立诊断入口，复用 `build_providers()` 与各 provider 的 `fetch()`，不另写探测逻辑；同时修正三个 provider 的错误分类与文案，让 doctor 与面板共用同一套自解释文案（DRY——doctor 不维护第二套措辞）。

**Tech Stack:** Python 3.12+、httpx、FastAPI、pytest、uv。

## Global Constraints

以下约束来自 `docs/specs/2026-08-04-diagnostics-design.md` 的「禁区」，每个任务都必须遵守：

- **不改 proxy 语义**：不配 = 直连且忽略环境变量。严禁让 provider 自动读 `HTTP_PROXY` 兜底。
- **不改 `_resolve_api_key()` 的优先级**（仍是 `api_key` → 环境变量 → 文件），只改文案与提示顺序。
- **不改兜底快照的触发条件**。
- **诊断输出、日志、异常消息中不得出现 token / key 及其任何片段**。
- 不新增任何第三方依赖。
- 不顺手重构无关代码。
- 中英双语文档必须同步更新，不允许只改一边。

---

### Task 1: Claude 的 403 与 401 分离

**Files:**
- Modify: `server/providers/claude.py:27`（新增常量）、`server/providers/claude.py:141-142`（分支拆分）
- Test: `tests/test_claude_provider.py`

**Interfaces:**
- Consumes: 现有 `error_usage(id, name, status, error)`（来自 `server/providers/base.py`）
- Produces: 常量 `_FORBIDDEN_MSG`；403 返回 `status="error"`，401 维持 `status="auth_expired"`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_claude_provider.py` 末尾追加。注意复用文件顶部已有的 `_make_provider` 与 `_write_credentials` 辅助函数：

```python
async def test_403_is_network_error_not_auth_expired(tmp_path):
    """403 是网络层拒绝，不能报成 token 过期——否则把人引向重新登录。"""
    provider = _make_provider(tmp_path, status_code=403)
    usage = await provider.fetch()
    assert usage.status == "error"
    assert "403" in usage.error
    assert "过期" not in usage.error
    assert "proxy" in usage.error


async def test_401_still_reports_auth_expired(tmp_path):
    """401 才是真的凭证问题，保留原有引导。"""
    provider = _make_provider(tmp_path, status_code=401)
    usage = await provider.fetch()
    assert usage.status == "auth_expired"
    assert "claude CLI" in usage.error
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_claude_provider.py -k "403_is_network or 401_still" -v`
Expected: `test_403_is_network_error_not_auth_expired` FAILED（当前 403 返回 `auth_expired`）

- [ ] **Step 3: 加常量**

在 `server/providers/claude.py` 第 27 行 `_AUTH_EXPIRED_MSG` 定义之后追加：

```python
_FORBIDDEN_MSG = (
    "HTTP 403：网络层拒绝，不是登录问题。该网络直连 api.anthropic.com 被拒，"
    "如需代理请在 config.toml 配 [providers.claude] proxy"
)
```

- [ ] **Step 4: 拆分分支**

把 `server/providers/claude.py` 里的：

```python
        if resp.status_code in (401, 403):
            return error_usage(self.id, self.name, "auth_expired", _AUTH_EXPIRED_MSG)
```

替换为：

```python
        if resp.status_code == 401:
            return error_usage(self.id, self.name, "auth_expired", _AUTH_EXPIRED_MSG)
        if resp.status_code == 403:
            return error_usage(self.id, self.name, "error", _FORBIDDEN_MSG)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_claude_provider.py -v`
Expected: 全部 PASS（含原有用例）

- [ ] **Step 6: 提交**

```bash
git add server/providers/claude.py tests/test_claude_provider.py
git commit -m "fix(claude): 403 不再误报 auth_expired，指向网络与代理配置"
```

---

### Task 2: Kimi 缺 key 提示区分运行环境

**Files:**
- Modify: `server/providers/base.py`（新增共享的 `running_as_service()`）
- Modify: `server/providers/kimi.py:25-27`（常量）、`server/providers/kimi.py:79-86`（fetch 开头）
- Test: `tests/test_kimi_provider.py`

**Interfaces:**
- Consumes: 现有 `_resolve_api_key()`（**不改其行为**）
- Produces: `running_as_service() -> bool`（定义在 `server/providers/base.py`，**Task 4 的 doctor 会导入同一个**）；常量 `_NO_KEY_MSG`、`_NO_KEY_MSG_SERVICE`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_kimi_provider.py` 末尾追加：

```python
async def test_no_key_message_under_systemd(monkeypatch):
    """以 systemd 服务运行时，必须明说环境变量这条路走不通。"""
    monkeypatch.setenv("INVOCATION_ID", "deadbeef")
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    provider = KimiProvider()
    usage = await provider.fetch()
    assert usage.status == "error"
    assert "systemd" in usage.error
    assert "api_key_file" in usage.error


async def test_no_key_message_in_plain_shell(monkeypatch):
    """普通进程里环境变量是可用选项，提示中应保留它。"""
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    provider = KimiProvider()
    usage = await provider.fetch()
    assert usage.status == "error"
    assert "systemd" not in usage.error
    assert "KIMI_API_KEY" in usage.error


async def test_key_resolution_priority_unchanged(monkeypatch, tmp_path):
    """回归护栏：优先级仍是 api_key → 环境变量 → 文件，本次只改文案。"""
    monkeypatch.setenv("KIMI_API_KEY", "sk-from-env")
    key_file = tmp_path / "secrets.sh"
    key_file.write_text('export KIMI_API_KEY="sk-from-file"\n', encoding="utf-8")
    assert KimiProvider(api_key="sk-direct", api_key_file=str(key_file))._resolve_api_key() == "sk-direct"
    assert KimiProvider(api_key_file=str(key_file))._resolve_api_key() == "sk-from-env"
    monkeypatch.delenv("KIMI_API_KEY")
    assert KimiProvider(api_key_file=str(key_file))._resolve_api_key() == "sk-from-file"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_kimi_provider.py -k "no_key_message" -v`
Expected: 两个新用例 FAILED（当前只有一条不区分环境的文案）

- [ ] **Step 3: 替换常量**

把 `server/providers/kimi.py` 里的：

```python
_NO_KEY_MSG = (
    "未配置 Kimi API key（config.toml 的 api_key / 环境变量 {env} / 配置的密钥文件 三者任一）"
)
```

替换为：

```python
# 提示按可靠性排序：config.toml 两条路在任何运行方式下都成立，环境变量则未必。
_NO_KEY_MSG = (
    "未配置 Kimi API key。请在 config.toml 配 [providers.kimi] api_key，"
    "或用 api_key_file 指向含该变量的文件，或导出环境变量 {env}"
)
# systemd 的 unit 只注入 PATH，读不到用户 shell 里的 export——
# 此时把环境变量列为选项会让人白折腾一轮，故单独给一份文案。
_NO_KEY_MSG_SERVICE = (
    "未配置 Kimi API key。当前以 systemd 服务运行，环境变量 {env} 不会被读到"
    "（unit 只注入 PATH）。请在 config.toml 配 [providers.kimi] api_key，"
    "或用 api_key_file 指向含该变量的文件"
)
```

- [ ] **Step 4: 加环境判断函数（放共享模块，Task 4 的 doctor 也要用）**

在 `server/providers/base.py` 末尾追加（**不要**放进 `kimi.py`——doctor 同样需要它，
两处各写一份必然随时间漂移）：

```python
def running_as_service() -> bool:
    """systemd 拉起的进程会带 INVOCATION_ID；据此判断环境变量那条路是否可用。"""
    return bool(os.environ.get("INVOCATION_ID"))
```

`base.py` 顶部需新增 `import os`。

然后在 `server/providers/kimi.py` 已有的 base 导入语句中加上它：

```python
from .base import ProviderUsage, UsageWindow, error_usage, parse_dt, running_as_service
```

> 执行前先读一遍 `kimi.py` 顶部真实的 import 行，按其实际导入的名字追加 `running_as_service`，不要照抄上面这行的其余部分。

- [ ] **Step 5: 改 fetch 里的分支**

把 `fetch()` 开头的：

```python
        key = self._resolve_api_key()
        if not key:
            return error_usage(
                self.id, self.name, "error", _NO_KEY_MSG.format(env=self._api_key_env)
            )
```

替换为：

```python
        key = self._resolve_api_key()
        if not key:
            template = _NO_KEY_MSG_SERVICE if running_as_service() else _NO_KEY_MSG
            return error_usage(
                self.id, self.name, "error", template.format(env=self._api_key_env)
            )
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/test_kimi_provider.py -v`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add server/providers/kimi.py tests/test_kimi_provider.py
git commit -m "fix(kimi): 缺 key 提示区分运行环境，systemd 下不再建议环境变量"
```

---

### Task 3: Codex 兜底快照说明原因

**Files:**
- Modify: `server/providers/codex.py:81-83`（兜底分支）
- Modify: `web/app.js:172-177`（stale 分支追加原因显示）
- Test: `tests/test_codex_provider.py`

**Interfaces:**
- Consumes: `_read_latest_session_usage() -> ProviderUsage | None`（**行为不变**）
- Produces: 常量 `_FALLBACK_NOTE`；兜底返回的 `ProviderUsage.error` 承载原因说明，`status` 仍为 `"stale"`

**设计说明：** `ProviderUsage` 没有 `note` 字段，只有 `UsageWindow` 有。为不改数据契约，原因说明放进已存在且已序列化的 `ProviderUsage.error` 字段——`status` 保持 `"stale"` 不变，因此不影响任何依据 status 的逻辑（poller 的退避、退出码判定）。前端相应地在 stale 且 error 非空时把原因显示出来。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_codex_provider.py` 末尾追加。该文件已有 `_write_session(tmp_path)` 辅助函数与
`_rpc_fail`（模拟 app-server 不可用）协程，直接复用，不要另造快照结构：

```python
async def test_fallback_explains_why_stale(tmp_path):
    """走兜底快照时要说明原因，否则用户只看到「多久前」，以为一切正常。"""
    _write_session(tmp_path)
    provider = CodexProvider(sessions_dir=str(tmp_path), rpc_call=_rpc_fail)
    usage = await provider.fetch()

    assert usage.status == "stale"
    assert usage.error is not None
    assert "app-server" in usage.error
    assert usage.windows, "兜底数据仍应带出额度窗口"
```

> 该文件已有走兜底路径的用例（断言 `usage.windows[0].used_pct == 25.0` 那个），
> 本用例只是在同样的路径上多断言一条 `error`。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_codex_provider.py -k fallback_explains -v`
Expected: FAILED（当前 `usage.error` 为 None）

- [ ] **Step 3: 加常量并在兜底处附上说明**

在 `server/providers/codex.py` 的 `_FALLBACK_ERROR` 常量之后追加：

```python
_FALLBACK_NOTE = (
    "数据来自本地会话快照——app-server 不可用。它需要连 chatgpt.com，"
    "如需代理请在 config.toml 配 [providers.codex] proxy"
)
```

把 `fetch()` 里的：

```python
        fallback = self._read_latest_session_usage()
        if fallback is not None:
            return fallback
```

替换为：

```python
        fallback = self._read_latest_session_usage()
        if fallback is not None:
            # status 仍是 stale，只借 error 字段把「为什么是旧数据」带给用户
            fallback.error = _FALLBACK_NOTE
            return fallback
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_codex_provider.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 前端在 stale 时显示原因**

`web/app.js` 中，error / auth_expired 分支会提前 `return card`，因此新增的 stale 说明要放在
**它之后、窗口渲染循环之前**。现有代码为：

```javascript
  if (p.status === "error" || p.status === "auth_expired") {
    const err = document.createElement("div");
    err.className = "card-error";
    err.textContent = p.error || "未知错误";
    card.appendChild(err);
    return card;
  }

  for (const w of p.windows) {
```

在 `}` 与 `for (const w of p.windows) {` 之间插入：

```javascript
  // stale 也可能带原因（如 Codex 走了本地快照）。数据照常渲染，只是多说一句为什么旧。
  if (p.status === "stale" && p.error) {
    const why = document.createElement("div");
    why.className = "card-error";
    why.textContent = p.error;
    card.appendChild(why);
  }

```

- [ ] **Step 6: 提交**

```bash
git add server/providers/codex.py web/app.js tests/test_codex_provider.py
git commit -m "fix(codex): 兜底快照说明 app-server 不可用，stale 有原因可循"
```

---

### Task 4: 新增 `server.doctor` 子命令

**Files:**
- Create: `server/doctor.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `resolve_config(cli_path) -> tuple[Config, ConfigSource]`（`server/config.py`）、`build_providers(cfg) -> list[Provider]`（`server/main.py`）、`ProviderUsage` 与 `running_as_service()`（`server/providers/base.py`，后者由 **Task 2 创建**，故本任务必须排在 Task 2 之后）
- Produces: `format_report(source: ConfigSource, results: list[ProviderUsage]) -> tuple[str, int]` 返回（报告文本, 退出码）；`main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_doctor.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_doctor.py -v`
Expected: FAILED with `ModuleNotFoundError: No module named 'server.doctor'`

- [ ] **Step 3: 实现 `server/doctor.py`**

```python
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
            lines.append(f"                · {window.label}: {window.used_pct}%")

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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_doctor.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 实跑一次确认输出可读**

Run: `uv run python -m server.doctor`
Expected: 打印三家状态；若本机 config.toml 齐全则全 `✓` 且退出码 0（`echo $?` 验证）

- [ ] **Step 6: 提交**

```bash
git add server/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): 新增部署自检子命令，一次报告三家通断与补救办法"
```

---

### Task 5: `install.sh` 接入自检

**Files:**
- Modify: `deploy/install.sh`（服务启动成功分支之后）

**Interfaces:**
- Consumes: `python -m server.doctor`（Task 4 产出）

- [ ] **Step 1: 改脚本**

`deploy/install.sh` 末尾现为：

```bash
if systemctl --user is-active --quiet ai-usage.service; then
    echo "✓ 服务已启动：http://127.0.0.1:$PORT"
    echo "  查日志：journalctl --user -u ai-usage -f"
    echo "  停/禁用：systemctl --user disable --now ai-usage.service"
else
```

在 `echo "  停/禁用：..."` 之后追加自检调用：

```bash
    echo
    # 装完立刻告诉用户三家分别通没通。诊断失败不等于安装失败，故 || true：
    # 没配 key、没配代理属于待办事项，不该让 install.sh 以非零退出。
    (cd "$PROJECT_DIR" && "$UV_BIN" run --no-sync python -m server.doctor) || true
```

- [ ] **Step 2: 实跑验证**

Run: `bash deploy/install.sh; echo "exit=$?"`
Expected: 服务启动信息之后打印 doctor 报告；`exit=0`

- [ ] **Step 3: 验证诊断有问题时仍以 0 退出**

Run:
```bash
mv config.toml /tmp/config.toml.bak
bash deploy/install.sh; echo "exit=$?"
mv /tmp/config.toml.bak config.toml
bash deploy/install.sh >/dev/null
```
Expected: 第一次跑时报告里出现 Claude 的 403 与 Kimi 的缺 key 提示，且 `exit=0`；恢复后再跑一次让服务回到正常配置

- [ ] **Step 4: 提交**

```bash
git add deploy/install.sh
git commit -m "feat(deploy): install.sh 装完跑一次自检，诊断失败不影响安装退出码"
```

---

### Task 6: 双语文档与 CHANGELOG

**Files:**
- Modify: `README.md`、`docs/README.zh-CN.md`、`CHANGELOG.md`、`docs/CHANGELOG.zh-CN.md`

- [ ] **Step 1: 两份 README 补 doctor 用法**

在两份 README 讲部署/安装的段落之后，各加一小节（英文版写英文，中文版写中文），内容须覆盖：

- 命令：`uv run python -m server.doctor`（支持 `--config PATH`）
- 用途：一次性报告三家通断、当前配置来源、以及每家缺什么怎么补
- `install.sh` 会在安装结束时自动跑一次
- 退出码语义：`0` = 无阻塞项（`~` 降级不算），`1` = 有 `✗` 项待处理

措辞风格照抄该文件既有小节，不要引入新的排版习惯。

- [ ] **Step 2: 两份 CHANGELOG 记录本次改动**

按各文件既有格式，在最新版本段落下记入三条：Claude 403 不再误报为登录过期；Kimi 缺 key 提示区分 systemd 环境；新增 `server.doctor` 自检并接入 `install.sh`。

- [ ] **Step 3: 全量测试**

Run: `uv run pytest`
Expected: 全绿

- [ ] **Step 4: 提交**

```bash
git add README.md docs/README.zh-CN.md CHANGELOG.md docs/CHANGELOG.zh-CN.md
git commit -m "docs: 补 doctor 自检说明与更新日志（中英双份）"
```

---

## 验收（对照 spec）

1. `uv run pytest` 全绿。
2. `uv run python -m server.doctor` 输出三家真实状态；临时移开 `config.toml` 后，能准确指出 Claude 403 真因与 Kimi key 缺失，且提示中**不出现**"token 已过期"字样。
3. `bash deploy/install.sh` 结束时展示诊断结果，诊断有问题时仍以 0 退出。
4. 中英双语 README 与 CHANGELOG 均已更新。
5. 未引入新的第三方依赖（`uv.lock` 无变化）。
