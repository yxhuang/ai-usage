# ai-usage

[![CI](https://github.com/yxhuang/ai-usage/actions/workflows/ci.yml/badge.svg)](https://github.com/yxhuang/ai-usage/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
![Local only](https://img.shields.io/badge/network-loopback%20only-brightgreen.svg)

**三家订阅，一个小窗，撞墙之前就知道。**

[English](../README.md) | 简体中文

同时付了 Claude Pro、ChatGPT Plus 和 Kimi 的钱，额度就散在三个地方，
而且哪一家都不会提前告诉你——等它说话的时候，你已经被限流了。
`ai-usage` 把三家读到一起，放进一个常驻的小面板。

<p align="center">
  <img src="panel-dark.png" width="380"
       alt="深色模式下的 ai-usage 面板：三张卡片各对应一家。Claude Pro 的 5 小时窗口 27%、周额度 48%、额外用量 credit 23%（$22.98 / $100）；Codex Plus 周额度 8%；Kimi 的 5 小时窗口 0%、周额度 15%。每根进度条上都有一道浅色竖线，标出时间窗口已经过去的比例。">
</p>

上图里每根条都落在自己那道竖线左边，说明三家都还有余量。那道竖线才是重点。

## 节奏刻度

那道细竖线是这个面板存在的理由。它标出的是**时间窗口已经过去了多少**。

用量条落在它左边，说明你花得比额度回补更慢，可以继续用；越过它，说明照这个速度会提前
用完。光看百分比只能知道「我用了多少」，节奏刻度回答的是「我还能不能这么用」——
周三下午两点你真正想知道的是后面这个问题。

进度条在正常档用各家的品牌色区分（Claude 珊瑚橙 / Codex 青绿 / Kimi 蓝），
但到了 70% 和 90% 一律切换成统一的警戒色——那两档是状态信号，不让品牌色盖掉。

## 它不做什么

**凭证不出本机。** daemon 读的是各家 CLI 自己写下的 token 文件，调的是对应厂商自己的
账户元数据接口，token 只在内存里待着。落盘的只有用量数字，而且服务在配置层面就拒绝
绑定回环地址以外的任何地址。

**跑它不花钱。** 这些是账户接口，不是推理接口，轮询不消耗任何对话额度。

## 快速上手

```bash
git clone https://github.com/yxhuang/ai-usage && cd ai-usage
uv sync
uv run python -m server.main     # 打开 http://127.0.0.1:8788
```

不用建配置文件也能跑。没登录的那家只是显示一张错误卡片，不影响其余两家。

想让它常驻（systemd user unit，开机自启）：

```bash
bash deploy/install.sh
journalctl --user -u ai-usage -f     # 看日志
```

## 环境要求

- **Python ≥ 3.11** 和 [uv](https://docs.astral.sh/uv/)
- 三家 CLI 至少装了一个并登录过。面板**只读复用**它们已有的凭证，
  自己没有登录流程，也永远不会问你要密码。

运行时依赖只有 `fastapi` / `uvicorn` / `httpx`；前端是纯 HTML/CSS/JS，
零构建链、零 node 依赖。

## 平台支持

| | 状态 |
|---|---|
| Linux | 主要目标平台，在 WSL2 Ubuntu 上开发和日常使用。 |
| macOS | 代码路径完全相同，应该能跑，但还没在真机上验证过，欢迎反馈。 |
| Windows | daemon 跑在 **WSL 里**，面板从 Windows 侧打开（见下）。WSL2 会自动转发 `localhost`，不用额外配网络。 |

daemon 本身是纯 Python，没有任何 OS 相关代码；Windows 那部分只关乎**怎么把窗口显示出来**。

## 做成桌面挂件

浏览器的 `--app` 模式本身就已经很像原生挂件了——没有地址栏，也没有标签栏。
Windows 上有两种开法，都写在 [deploy/windows-shortcut.md](../deploy/windows-shortcut.md)：

**一、一个快捷方式就够。** `chrome.exe --app=http://localhost:8788 --window-size=370,640`，
一个干净的小窗。

**二、收进系统托盘** —— [`deploy/tray-widget.ps1`](../deploy/tray-widget.ps1)。
窗口不占任务栏和 Alt+Tab；按标题栏的关闭按钮是收进托盘而不是退出；双击托盘图标切换显隐，
再打开时回到你上次放的位置和尺寸。

**零安装**：只用系统自带的 PowerShell + WinForms + `user32.dll`，不引入 Electron、
Tauri 之类的运行时；而且它驱动的就是同一个 `localhost:8788` 页面，不存在第二套界面要维护。
[`deploy/install-widget.ps1`](../deploy/install-widget.ps1) 可以把已有的快捷方式一键改指过去。

代价要知道：它是**从外部操纵 Chrome 的窗口**，属于 hack——脚本必须常驻，
Chrome 大版本升级若改了窗口结构有失灵的可能。标题栏根本砍不掉（Chrome 自绘），
不过这反倒成了好事：没有标题栏就没有拖拽区，窗口挪不动。

## 三家的数据从哪来

| Provider | 取数方式 | 说明 |
|---|---|---|
| Claude | `GET api.anthropic.com/api/oauth/usage` | 复用 claude CLI 的 OAuth token；返回 5 小时 / 周窗口，以及独立计费的 extra credit 池 |
| Codex | 临时拉起 `codex app-server`，JSON-RPC 调 `account/rateLimits/read` | 取完即关，不常驻；失败自动降级为解析本地 session 记录里的最近一条限额快照（标 `stale` + 数据时刻） |
| Kimi | `GET api.kimi.com/coding/v1/usages` | 用 `sk-kimi-*` API key；报文只给绝对值，百分比由本地换算 |

各 provider 完全独立：一家挂了只有那张卡片显示错误态，其余照常。
轮询默认 300 秒一次，单家失败指数退避（最长 30 分钟），不影响其他家。

**这些都是非官方接口。** 三个端点没有一个是有文档的公开 API，
任何一家厂商都可能随时改动或下线。详见下面的免责声明。

## 配置

所有配置项都是可选的，见 [config.example.toml](../config.example.toml)；
不建 `config.toml` 就用内置默认值。几个值得知道的：

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
uv run pytest          # 68 项测试；不联网、不读真实凭证
```

测试用 `httpx.MockTransport` 与临时目录构造报文和假凭证。
`tests/conftest.py` 会从测试进程里剥掉凭证类环境变量——
这条防护来自一次真实事故：某条用例没清理环境变量，
把开发机上的真实 key 打进了断言失败输出。测试永远不该看见真实凭证。

## 现状

还很早期，这点得说清楚。逻辑有 68 项离线测试覆盖，但目前只在一台机器上跑过。
如果某家的报文结构在你的账号上不一样（不同套餐档位、不同区域），
带上脱敏后的报文提个 issue 会非常有帮助。

v1 不做：历史曲线、阈值告警、Tauri / Electron 壳、OAuth token 自动续期、多用户与远程访问。

## 参与

欢迎 issue 和 PR，尤其欢迎来自另一台机器的反馈、或者一个 macOS 的可用性确认。
改代码的话注意：绝对路径 `/home/<user>` 不应该出现在提交里。

## 免责声明

`ai-usage` 是个人的非官方工具，**与 Anthropic、OpenAI、Moonshot AI 均无隶属、
背书或支持关系**。文中出现的产品名仅用于指代它所读取的服务。

它依赖的是三家未公开文档的账户元数据接口，厂商随时可能改动或下线；
它也会读取这些 CLI 管理的凭证文件——只读，且只把凭证发回它本来所属的那家厂商。
即便如此，使用风险由你自己承担，是否符合服务条款也需要你自己把关。
软件按现状提供，不附带任何担保。见 [LICENSE](../LICENSE)。

## 致谢

由它所监控额度的那几位助手一起做出来的：Claude Code、Codex CLI、Kimi CLI。
设计文档：[docs/specs/2026-07-26-ai-usage-design.md](specs/2026-07-26-ai-usage-design.md)。

## 许可

[MIT License](../LICENSE)。

---

⭐ 如果它让你少撞了一次限流，点个 star 能帮别人找到它。
