# 部署诊断改造：让错误信息指向真因

日期：2026-08-04

## 背景

在一台新机器上部署本项目后，面板里 Claude 与 Kimi 两家都不出数，Codex 看着正常。
实际排查下来是**一个根因、三种表现**——本机没有 `config.toml`（该文件在 `.gitignore`
内，按设计不入库，因此无法随仓库分发）：

| 表现 | 用户看到的 | 真实原因 |
|---|---|---|
| Claude | `auth_expired`：「OAuth token 已过期——随便用一次 claude CLI 即自动续期」 | token 完全有效。默认 proxy 为空时 httpx 显式 `trust_env=False`，裸连 `api.anthropic.com` 得 **403**，而 `claude.py` 把 401 和 403 一并映射成 `auth_expired` |
| Kimi | 「未配置 Kimi API key（config.toml 的 api_key / 环境变量 KIMI_API_KEY / 配置的密钥文件 三者任一）」 | 提示本身没错，但**环境变量那条在 systemd 服务下不可能生效**——`deploy/ai-usage.service` 只注入 `PATH`，读不到用户 shell 里的 export |
| Codex | 有数字，状态 `stale` | app-server 连不上，全靠 `sessions_dir` 兜底快照撑着。UI 只显示「多久前」，不说**为什么**是旧数据 |

三条错误信息没有一条把人指向真因：Claude 那条主动把人引向"重新登录"这个错误方向，
Kimi 那条会让人白白去 export 一遍环境变量，Codex 那条则让人以为一切正常。

排查耗时的根源不是文档缺失（README 的 prerequisites 表格、proxy 语义、
`config.example.toml` 都写了），而是**部署完成那一刻没有任何反馈**，以及
**运行时的错误信息不足以自我解释**。

## 目标

1. 任一机器部署后，用户能在**安装脚本结束时**就知道三家分别通没通、缺什么、怎么补。
2. 面板里出现的每一条错误信息，都能把人指向真正的原因，而不是相反的方向。
3. 不改变任何现有行为语义——proxy 依然是"不配就是直连且忽略环境变量"，
   兜底快照依然照常兜底。本次只改**错误分类与文案**，以及**新增**一个诊断入口。

## 核心设计：`server.doctor` 子命令

新增 `python -m server.doctor`，一次性报告三家的可达性与配置状态。选它而不是把逻辑
内联进 `install.sh`，理由有三：

- **可测试**：走 `tests/`，与 provider 逻辑同样受回归保护；写死在 shell 里的诊断测不到。
- **长期可用**：不只服务于安装那一刻，用户日后任何时候都能跑它自查，无需打开面板猜。
- **风格一致**：与既有的 `server.launch`、`--config` 保持同一种模块化组织方式。

输出契约（stdout，人类可读，非 JSON）：

```
ai-usage doctor

配置          config.toml 未找到，使用内置默认值
              模板：cp config.example.toml config.toml
运行方式      systemd 服务（环境变量来自 unit，不含你 shell 里的 export）

Claude        ✗ HTTP 403 —— 网络层拒绝，不是登录问题
              该网络裸连 api.anthropic.com 被拒。若需代理，在 config.toml 配
              [providers.claude] proxy = "http://127.0.0.1:7890"
Codex         ~ 数据来自本地会话快照（app-server 不可用）
              app-server 要连 chatgpt.com；若需代理，配 [providers.codex] proxy
Kimi          ✗ 未配置 API key
              当前以 systemd 服务运行，环境变量 KIMI_API_KEY 不会被读到。
              请用 config.toml 的 api_key，或 api_key_file 指向含该变量的文件

3 项中 2 项需要处理。详见 README「Configuration」。
```

三种标记的含义与退出码的关系必须明确：

| 标记 | 含义 | 计入退出码 |
|---|---|---|
| `✓` | 正常出数 | 否 |
| `~` | 能出数但有降级（如 Codex 走兜底快照） | **否**——有数据可用，不算待办 |
| `✗` | 完全取不到数据 | **是** |

退出码：无 `✗` 时 `0`；有任一 `✗` 时 `1`（便于 CI 或脚本判断，但 `install.sh`
不因此中断——诊断失败不等于安装失败）。

doctor 复用 provider 的 `fetch()`，不另写一套探测逻辑，避免两处实现漂移。

## 改动清单

### 代码

**`server/providers/claude.py`**
- 拆开 401 与 403。401 维持现有 `auth_expired` 与文案（token 确实过期时那条建议是对的）。
- 403 改为 `status="error"`，文案指出这是网络层拒绝而非登录问题，并点名
  `providers.claude.proxy` 这个配置项。
- 保留代码里那条注释的事实判断：直连可用与否取决于所在网络，不是普遍事实。

**`server/providers/kimi.py`**
- 缺 key 的提示改为按可靠性排序：`config.toml` 的 `api_key` → `api_key_file` → 环境变量。
- 检测 `INVOCATION_ID`（systemd 注入的变量）判断是否以服务方式运行；是则明确告知
  环境变量这条路不可用，避免用户白折腾。
- 仅改文案与顺序，`_resolve_api_key()` 的解析优先级**不变**（仍是 api_key → 环境变量 → 文件）。

**`server/providers/codex.py`**
- 走 `_read_latest_session_usage()` 兜底时，在返回的 usage 上带一条 note，
  说明数据来自本地会话快照且 app-server 不可用。
- 兜底行为本身不变。

**`server/doctor.py`（新增）**
- 加载配置、逐个跑 provider 的 `fetch()`、按上面的输出契约打印。
- 报告配置来源（复用 `ConfigSource`）与运行方式（`INVOCATION_ID` 判断）。
- 绝不打印 token、key 或其任何前缀——沿用项目既有的凭证保密约束。

### 部署

**`deploy/install.sh`**
- 服务启动成功后追加一次 `python -m server.doctor` 调用，输出直接展示给用户。
- doctor 返回非零**不影响** install.sh 的退出码：三家没配好属于待办事项，不是安装失败。

### 文档

- `README.md` 与 `docs/README.zh-CN.md`：在部署段落说明 doctor 的用法与用途。
- `CHANGELOG.md` 与 `docs/CHANGELOG.zh-CN.md`：记录本次改动。
- 双语两侧必须同步，不允许只改一边。

### 测试

- `tests/test_claude_provider.py`：403 与 401 分别断言状态与文案，确认 403 不再报 `auth_expired`。
- `tests/test_kimi_provider.py`：有无 `INVOCATION_ID` 两种情况下的提示差异；
  并断言取 key 的优先级未变。
- `tests/test_codex_provider.py`：兜底路径带上 note。
- `tests/test_doctor.py`（新增）：正常、缺 key、403 三种场景的输出与退出码；
  断言输出中**不含**任何凭证字样。

## 验收标准

1. `uv run pytest` 全绿，新增测试覆盖上述场景。
2. `uv run python -m server.doctor` 在本机输出三家真实状态；把 `config.toml` 临时移开后，
   输出能准确指出 Claude 403 的真因与 Kimi 的 key 缺失，且提示中不出现"token 已过期"字样。
3. `bash deploy/install.sh` 结束时展示诊断结果，且在诊断有问题时仍以 0 退出。
4. 中英双语 README 与 CHANGELOG 均已更新。
5. 未引入任何新的第三方依赖。

## 禁区

- 不改 proxy 语义：不配 = 直连且忽略环境变量。**不允许**让 provider 自动读
  `HTTP_PROXY` 兜底——那会让"直连"名不副实，与既有设计冲突。
- 不改 `_resolve_api_key()` 的优先级，不改兜底快照的触发条件。
- 不动 `config.toml` 的 gitignore 状态，不把任何密钥写进仓库。
- 诊断输出不得包含 token / key 及其片段。
- 不顺手重构无关代码。
