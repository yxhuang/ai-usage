# 自启动开关设计

2026-08-02

## 目标

面板里加一个可开关的「登录时自动启动」选项，跨 Windows / macOS / Linux 通用。

硬约束，来自用户：**不开启就不碰用户系统**。默认关闭；只有用户显式点开关才写入启动项；
关闭时干净移除，不留残迹。

## 非目标

- **托盘图标的跨平台实现**。原生 GUI 各平台完全不同，成本远超收益。Windows 现有的
  `deploy/tray-widget.ps1` 保留为可选增强，不纳入本功能。
- **配置写回能力**。开关状态不进 `config.toml`，见「状态即事实」。
- 启动延迟、条件启动等高级选项。

## 架构

新增 `server/autostart.py`，对外三个动作：

```python
def status() -> AutostartStatus   # 查
def enable() -> None              # 开
def disable() -> None             # 关
```

内部按平台分派到不同 backend，每个 backend 实现同一组接口。调用方不感知平台。

```
server/autostart.py
├── detect_platform()        → windows | macos | linux-systemd | linux-xdg | wsl | unsupported
├── WindowsBackend           启动文件夹 .vbs
├── MacBackend               ~/Library/LaunchAgents/*.plist
├── SystemdBackend           ~/.config/systemd/user/*.service
└── XdgBackend               ~/.config/autostart/*.desktop
```

### 状态即事实

`status()` **不读任何自己写的配置**，直接查启动项在操作系统里是否存在：

| 平台 | 判定依据 |
|---|---|
| Windows | 启动文件夹里的 `.vbs` 是否存在 |
| macOS | `~/Library/LaunchAgents/` 里的 plist 是否存在（登录时该目录会被自动加载） |
| Linux systemd | unit 文件存在 **且** `systemctl --user is-enabled` 为 enabled |
| Linux XDG | `~/.config/autostart/` 里的 `.desktop` 是否存在 |

好处：用户手动删掉启动项，界面立刻如实反映；不存在「配置说开着、实际没开」的撒谎状态；
也省掉给配置加写回能力的整块工作。

## 启动器

启动项拉起的**不是 daemon 本身**，而是 `server/launcher.py`，它做两件事：

1. 确保 daemon 在跑 —— 先探测端口，已在跑就跳过（幂等，这点很重要：用户可能已经用别的
   方式起了 daemon，例如 WSL 下的 systemd）。
2. 用浏览器的 app 模式打开面板窗口。

浏览器探测顺序：Chrome → Edge → Chromium → 系统默认。**全部找不到时降级为只起 daemon，
并在下次面板打开时于设置区显示一行说明** —— 不静默失败。

窗口层因此天然跨平台：面板本来就是网页，三个平台都能以 app 模式打开一个 URL。

### 路径快照与失效

`enable()` 时把 `sys.executable` 与项目根目录**快照**进启动项文件，不依赖 `uv`、不依赖
PATH。代价是移动项目目录后启动项失效。`status()` 因此额外校验快照路径是否仍存在，
失效时返回 `stale`，界面提示「重新开关一次即可修复」。

## 平台实现细节

| 平台 | 文件 | 备注 |
|---|---|---|
| Windows | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ai-usage.vbs` | VBS 而非 .lnk：能静默启动不闪黑窗，且是纯文本、便于校验与清理 |
| macOS | `~/Library/LaunchAgents/io.github.ai-usage.plist` | `RunAtLoad=true`；enable 时顺带 `launchctl load` 使当前会话立即生效 |
| Linux systemd | `~/.config/systemd/user/ai-usage.service` | `systemctl --user enable`；见下方 linger 说明 |
| Linux 无 systemd | `~/.config/autostart/ai-usage.desktop` | XDG 规范，主流桌面环境通用 |

### systemd 的 linger

用户级 service 要在无登录会话时运行需要 linger。`enable()` 会尝试
`loginctl enable-linger`，**但这一步可能因 polkit 策略需要提权**。失败不视为整体失败：
启动项照常写入，`status()` 返回 `linger_missing`，界面提示用户手动执行那条命令。

### WSL

按已确认的决策：**只管 daemon 层，窗口层不管**。

WSL 走 SystemdBackend，但界面上的文案改为「随 WSL 启动」而非「登录时启动」——
WSL 实例本身不随 Windows 开机，而是被 VSCode Remote-WSL 或 wsl 终端按需拉起，
「登录自启」在这里会误导。窗口层指向 `deploy/windows-shortcut.md`。

检测：`/proc/version` 含 `microsoft`，或存在 `WSL_DISTRO_NAME`。

## API

```
GET  /api/autostart   → {supported, enabled, platform, note, issues: []}
PUT  /api/autostart   body {"enabled": bool}  → 同上结构
```

`issues` 承载 `linger_missing` / `no_browser` / `stale` 等非致命问题，界面据此显示提示。

`PUT` 而非 `POST`：非简单方法，跨域时强制触发预检。

## 安全

这是本设计里风险最高的部分：**一个本地 HTTP 端点获得了往用户系统写持久化启动项的能力**。
`127.0.0.1:8788` 并非只有本面板能访问 —— 任何网页都能对本地端口发请求，CORS 只挡住
读取响应，简单请求的副作用照样发生。

防护做成一个 FastAPI 依赖 `require_local_ui`，**同时套用到 `PUT /api/autostart` 和现有的
`POST /api/refresh`**：

1. **Host 头校验** —— 必须是 `localhost` 或 `127.0.0.1`（含端口）。挡 DNS rebinding。
2. **Origin 头校验** —— 存在时必须匹配本机来源；缺失时放行（curl 等非浏览器工具不带
   Origin，而恶意网页发起的请求必定带）。显式拒绝 `null`。
3. **自定义头** —— 要求 `X-Requested-By: ai-usage-panel`。跨域简单请求无法携带自定义头，
   会被迫走预检，而服务端不返回任何 CORS 头，预检必然失败。这是主防线。

服务端不注册 CORS 中间件（当前也没有），保持"任何跨域请求都拿不到许可"。

## 界面

面板底部一个克制的设置区：一个开关 + 一行说明文字。README 强调面板「只有一处是响的」
（节奏刻度），设置区不该抢戏——默认收起，点齿轮展开。

- 不支持的平台：开关禁用 + 说明原因
- `issues` 非空：开关下方一行黄字提示
- 操作失败：就地显示错误原因，不弹窗

## 错误处理

- `enable()` 写入失败（权限、目录不存在）→ 抛出带明确原因的异常，API 转 500 + 原因，
  界面就地显示。
- 不做部分成功：写文件成功但 `systemctl enable` 失败时，回滚已写入的文件，保证
  「要么干净开启，要么什么都没变」。
- `status()` 任何情况下不抛异常，最坏返回 `supported=False` + 原因。

## 测试

新增 `tests/test_autostart.py`，沿用现有 pytest 约定：

- 用 `monkeypatch` 假冒 HOME 与平台检测，逐个 backend 测 enable → status → disable 全循环
- 断言 disable 后目录**完全干净**（这是对用户「可随时取消恢复」承诺的直接验证）
- 重复 enable 幂等；未 enable 时 disable 不报错
- 路径失效场景返回 `stale`

安全依赖的用例并入 `tests/test_api.py`：Host/Origin/自定义头的各种组合，
以及现有 `/api/refresh` 加固后不回归。

## 卸载

- README 增加一节：三平台的启动项路径与手动清理方法
- CLI 兜底 `python -m server.autostart disable`——万一面板打不开了仍能关掉自启动
