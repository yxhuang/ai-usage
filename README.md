# ai-usage

一屏看清 Claude / Codex / Kimi 三家订阅的额度水位与重置时间。

WSL（或任意 Linux / macOS）里跑一个轻量 daemon，浏览器 `--app` 模式开成无地址栏小窗，
观感接近原生 widget。只调各家的**账户元数据接口**，不消耗任何对话额度。

```
┌─────────────────────────────┐
│ AI 额度        更新于 15:42 ⟳│
│ ┌─────────────────────────┐ │
│ │ Claude · Pro            │ │
│ │ 5 小时窗口         39.0%│ │
│ │ ▓▓▓▓▓▓▓▓▎  ┆            │ │← 竖线是「节奏刻度」
│ │ 周三 19:29 重置 · 剩 3 小时│ │
│ │ 周额度             44.0%│ │
│ │ ▓▓▓▓▓▓▓▓▓▓▎   ┆         │ │
│ └─────────────────────────┘ │
│ ┌ Codex · Plus ───────────┐ │
│ └ Kimi · …    ────────────┘ │
└─────────────────────────────┘
```

**节奏刻度**是这个面板和普通用量条的区别：那道细竖线标出「时间窗口已经过去多少」。
填充越过刻度，说明你消耗得比额度回补更快；落在刻度左边，说明还有余量。
它把「我用了多少」变成了「我还能不能这么用」。

## 依赖

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/)
- 三家的 CLI 已各自登录过（面板**只读复用**它们已有的凭证，自己不做登录流程）

运行时依赖只有 `fastapi` / `uvicorn` / `httpx`；前端是纯 HTML/CSS/JS，零构建链、零 node 依赖。

## 快速开始

```bash
git clone <repo> ai-usage && cd ai-usage
uv sync
cp config.example.toml config.toml   # 可选，不建也能按默认值跑
uv run python -m server.main         # 打开 http://127.0.0.1:8788
```

常驻使用（systemd user unit，开机自启）：

```bash
bash deploy/install.sh
journalctl --user -u ai-usage -f     # 看日志
```

Windows 侧开成小窗的做法见 [deploy/windows-shortcut.md](deploy/windows-shortcut.md)。

## 三家的数据从哪来

| Provider | 取数方式 | 说明 |
|---|---|---|
| Claude | `GET api.anthropic.com/api/oauth/usage` | 复用 claude CLI 的 OAuth token；返回 5 小时 / 周窗口，以及独立计费的 extra credit 池 |
| Codex | 临时拉起 `codex app-server`，JSON-RPC 调 `account/rateLimits/read` | 取完即关，不常驻；失败自动降级为解析本地 session 记录里的最近一条限额快照（标 `stale` + 数据时刻） |
| Kimi | `GET api.kimi.com/coding/v1/usages` | 用 `sk-kimi-*` API key；报文只给绝对值，百分比由本地换算 |

各 provider 完全独立：一家挂了只有那张卡片显示错误态，其余照常。
轮询默认 300 秒一次，单家失败指数退避（最长 30 分钟），不影响其他家。

## 配置

所有配置项见 [config.example.toml](config.example.toml)，不建 `config.toml` 就用内置默认值。
几个值得知道的：

- `server.port`：默认 `8788`。`server.host` **只接受回环地址**，填别的会直接拒绝启动。
- `providers.claude.proxy`：Anthropic 走代理时在这里填。**必须显式配**——
  daemon 是非交互进程，不读 shell 配置文件，不会自动继承代理环境变量。
- `providers.kimi`：key 按 `api_key` → 环境变量 → 密钥文件三级回退，第一个取到的生效。
  密钥文件是用正则提取变量值，**不会 source/exec** 它。
- `providers.codex.command`：拉起 app-server 的命令，可换成自己的包装脚本。

## 安全

- 服务只绑回环地址，配置层面强制校验，不给「不小心暴露到局域网」留口子。
- 凭证**只读复用**各 CLI 已有的文件，token 只在内存里，绝不落盘、绝不入日志。
  落盘缓存 `data/cache.json` 只有用量数字。
- 日志遇到异常只记 provider 与异常类型，不记异常正文和 traceback
  （正文可能夹带带 token 的 URL）。
- 不读取、不展示、不存储任何账号身份信息（邮箱、组织 ID、用户 ID）。
- `config.toml` 与 `data/` 已在 `.gitignore`；若把 key 直接写进 `config.toml`，请 `chmod 600`。

## 开发

```bash
uv run pytest          # 全部测试；不联网、不读真实凭证
```

测试用 `httpx.MockTransport` 与临时目录构造报文和假凭证。
`tests/conftest.py` 会从测试进程里剥掉凭证类环境变量——
这条防护来自一次真实事故：某条用例没清理环境变量，
把开发机上的真实 key 打进了断言失败输出。测试永远不该看见真实凭证。

## v1 不做

历史曲线、阈值告警、系统托盘图标、Tauri 壳、OAuth token 自动续期、多用户与远程访问。

设计文档：[docs/specs/2026-07-26-ai-usage-design.md](docs/specs/2026-07-26-ai-usage-design.md)
