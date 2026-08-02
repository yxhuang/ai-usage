# 自启动开关设计

2026-08-02

> ## ⚠️ 状态：本文尚未定稿，**不要照此实现**
>
> 经 Codex 只读评审，结论为「需修改后开工」：3 项阻断、10 项重要。下方原文保留作为
> 出发点，**必须先按〈评审待修订项〉整体修订并重新评审**。
>
> 前置依赖已完成：公版默认值改造（`2026-08-02-public-defaults-design.md`，已合入
> `da153ff`）。评审明确指出它是本功能的前置——否则自启动会把一套带私人默认、
> 在外部机器必然异常的 daemon 固化进持久启动项。

## 评审待修订项

### 阻断（必须先解决）

1. **点击劫持绕过全部防线**。现有安全设计只防"恶意网页自己发跨域请求"。攻击者可把
   本地面板放进透明 iframe 诱导用户点真开关——请求由面板自身发出，Host、Origin、
   自定义头全部合法。修法：首页响应必须下发 `Content-Security-Policy: frame-ancestors 'none'`
   （必须是 HTTP 响应头，不能用 `<meta>`）+ `X-Frame-Options: DENY` 兼容旧浏览器；
   首次开启建议再加一次明确确认。
2. **unit 撞名会误伤既有部署**。本文计划写入 `~/.config/systemd/user/ai-usage.service`，
   而 `deploy/install.sh` 用的正是同一路径同一名字。老用户装过之后，`status()` 会误报
   「已开启」，`enable()` 可能覆盖、`disable()` 可能删除用户原有部署——直接违反
   「不开启不碰系统」与「可完全恢复」。修法：改用独立名称（如
   `ai-usage-panel-autostart.service`）；生成文件里写入格式版本、项目根路径与
   ownership marker；状态区分 `owned / external / stale / conflict / disabled`；
   任何无法确认归属的文件一律不覆盖不删除，返回 409 并给出手工处理说明。
   ⚠️ 仅改名字不够——两个 unit 会抢同一端口，需设计迁移与冲突规则。
3. **linger 语义相反且无法保证恢复**。用户管理器本来就会在首次登录时启动 user service；
   linger 的作用是「开机即启动 + 最后一次注销后继续运行」，恰是本功能不想要的常驻。
   它还修改独立的系统状态（用户原本就开着 linger 时不能在关闭本功能时关掉它），
   且 linger 在图形登录前启动，浏览器窗口拿不到 DISPLAY/Wayland 会话。
   修法：**彻底砍掉 linger**。daemon 用 systemd user unit，登录后开窗口用 XDG autostart。

### 重要

4. **macOS**：`~/Library/LaunchAgents` + `RunAtLoad` 可行，但「plist 存在 = 已启用」不成立
   ——macOS 13+ 允许用户在「登录项」里禁用而保留 plist。且本文只写了 enable 时 load、
   没写 disable 时 unload，删 plist 不会可靠停止已加载的 job。修法：注册/注销成对处理
   （bootstrap/bootout 或 load/unload）；查不到授权状态时返回 `authorization_unknown`，
   不得武断报 `enabled=true`；plist 权限不得 group/world writable。
5. **Windows**：启动目录路径正确，但应经 `FOLDERID_Startup` 解析以适配重定向场景；
   **VBScript 已进入微软退役流程**，改用 `.lnk` 指向 `pythonw.exe`；Windows 允许在设置
   或任务管理器中禁用启动项而不删文件，故「文件存在 = 启用」同样不可靠，状态文案要区分
   「已注册」与「系统确认会运行」。
6. **Windows 覆盖面**：README 当前要求 Windows 用户把 daemon 跑在 WSL，项目从未在原生
   Windows 上验证过。**用户已决定：做 Windows backend 但不实机验证**，因此必须在文档与
   UI 上明确标注 experimental，不得把未验证路径包装成已支持。
   （代码层面已确认无 Unix 硬依赖：路径全走 `expanduser()`；唯一风险是 codex 若装成
   `.cmd` 包装器则子进程可能起不来，但有读会话日志的兜底降级。）
7. **Linux XDG**：应优先 `$XDG_CONFIG_HOME/autostart`，未设置时才回落 `~/.config/autostart`；
   `Hidden=true`、无效 `TryExec`、`OnlyShowIn/NotShowIn` 都会让文件存在却不运行。
   **backend 应按功能选择而非按「有没有 systemd」**：daemon 用 systemd，GUI 窗口用 XDG。
8. **launcher 生命周期契约缺失**：没说明它是 exec daemon、常驻监督、还是启动子进程后退出
   ——对 systemd/launchd 三者语义完全不同，会导致 daemon 被误判启动完成、成为失管子进程
   或被服务管理器清理。且「只探测端口打开」会把占用 8788 的无关服务误认成 ai-usage，
   探测与启动之间还有竞态。修法：后台管理器直接监督 daemon，开窗口作为独立的登录后动作；
   探测改用 ai-usage 专用健康端点并等待就绪；用进程锁处理并发启动。
9. **状态模型**：`status()` 出错时返回 `supported=False` 会混淆「平台不支持」与
   「查询临时失败」，还可能禁用开关导致用户无法从 UI 清理残留项。改三态
   `enabled: true | false | null`，另设 `supported` / `state` / 错误码。
10. **失败处理**：「要么成功要么没变化」目前无法保证——文件写入、reload、enable、
    launchctl 注册、开窗口是多套独立系统操作，启动浏览器后无法回滚。修法：序列化
    enable/disable；同目录原子替换并拒绝跟随异常 symlink；所有系统命令设超时；
    先完成可回滚的注册再提交状态，开窗口只作 best-effort。重复 enable 对 owned 项 no-op、
    对 foreign 项报冲突；disable 缺失项 no-op。
    「目录完全干净」的测试断言应改为「恢复到操作前快照」——`LaunchAgents`、`autostart`、
    `systemd/user` 是共享目录，不能因为变空就删掉。
11. **路径快照**：只验证解释器与项目目录存在，不足以判断可启动（launcher 模块、依赖、
    配置可能已失效）；服务管理器环境极精简（现有 unit 已专门补 PATH）。修法：状态应解析
    并校验自己生成的启动项内容；保存格式版本；用平台原生序列化（plist 用 `plistlib`，
    desktop/systemd 遵守各自转义规则），**不得用字符串拼 shell 命令**。
12. **安全细节收紧**：`PUT` + 自定义头确实能让普通恶意网页无法发出请求（预检会失败），
    但「Origin 缺失即放行」是不必要的 fail-open——CLI 兜底可直接调 backend，无需为 curl
    放宽。Host 白名单需补 `[::1]:port`（配置已允许 `::1`），并拒绝缺失/重复/畸形值，
    不信任 `X-Forwarded-Host`。
13. **测试**：纯 monkeypatch 循环不足以支撑公开的跨平台承诺。需补：关闭状态查询零写入、
    旧 `ai-usage.service` 冲突、foreign/损坏/symlink 文件、每步失败后的回滚、disable
    半成功重试、并发 PUT、命令超时、IPv6、缺失 Origin、预检、CSP/X-Frame-Options、
    操作前已有的无关文件保持原样。
14. **范围**：建议直接砍掉 linger、enable 后立即 `launchctl load` 开第二个窗口、复杂浏览器
    枚举与 `no_browser` 历史状态；推荐拆两阶段——先做「daemon 登录自启」（由 OS 直接监督
    daemon），再做「登录后自动开窗口」（Linux 用 XDG，macOS/Windows 用各自 GUI 登录机制）。
    `POST /api/refresh` 的加固可独立提交，不必绑在本功能里。

### 评审确认无误的部分

Windows 启动文件夹默认路径、用户级 LaunchAgent、XDG autostart 三种机制本身可行；
「不另存布尔配置、直接查询操作系统事实」的方向正确——问题在于当前查询的「事实」
过于粗糙，且未处理既有项归属与系统级禁用状态。应保留：不写 `config.toml`、
读取系统真实状态、唯一且可验证的 owned artifact、CLI disable 兜底、
Host/Origin/自定义头三重校验、禁止 iframe。

---

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
