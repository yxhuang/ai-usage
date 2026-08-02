# 自启动开关设计（v2）

2026-08-02

> **v2 修订说明**：v1 经 Codex 只读评审判为「需修改后开工」（3 阻断 + 10 重要 + 1 范围）。
> 本文是按那 14 项重写的版本，不是在 v1 上打补丁——原文已整体替换。
> 逐项处置见文末〈评审项处置对照〉，便于复审核对。
>
> 前置依赖已完成：公版默认值改造（`2026-08-02-public-defaults-design.md`，已合入
> `da153ff`）。没有它，自启动只会把一套带私人默认、在外部机器必然报错的 daemon
> 固化进持久启动项。

## 目标

面板里加一个可开关的「登录时自动启动」选项，跨 Windows / macOS / Linux 通用。

硬约束，来自用户，优先级高于任何功能考量：

- **不开启就不碰用户系统**。默认关闭；只有显式点开关才写文件。
- **随时可取消**。关闭后恢复到操作前的状态。
- **绝不误伤不属于本功能的东西**。这条是 v1 最大的漏洞（见〈归属与冲突〉）。

## 范围：分两阶段，本文只定阶段一

| 阶段 | 内容 | 状态 |
|---|---|---|
| **一** | 登录时自动启动 **daemon**（由操作系统的服务管理器直接监督） | 本文定义，可开工 |
| 二 | 登录后自动**打开面板窗口** | 只列出待解问题，不在本文定稿 |

拆分的理由：阶段一自成闭环，且由 OS 直接 `ExecStart` daemon，不需要中间 launcher，
v1 里「launcher 生命周期未定义」「端口探测误判」「探测与启动竞态」三个问题**整类消失**。
阶段二真正的难点（无头环境拿不到 GUI 会话、浏览器枚举、窗口重复打开）与阶段一无耦合，
拖着它会让一个能交付的功能卡在最不确定的部分上。

对本仓库作者（WSL）而言两阶段都不改变现状：daemon 已由 `deploy/install.sh` 的 systemd
unit 管着，窗口已由 `~/.vscode-server/server-env-setup` → `ai-usage-panel` → `launcher.vbs`
拉起。本功能面向的是外部用户。

## 非目标

- 跨平台托盘图标。原生 GUI 各平台完全不同，成本远超收益。Windows 现有的
  `deploy/tray-widget.ps1` 保留为可选增强，不纳入本功能。
- 把开关状态写进 `config.toml`。见〈状态即事实〉。
- 启动延迟、条件启动等高级选项。
- linger。**v1 的 linger 方案整体删除**，理由见〈为什么不碰 linger〉。

---

## 一、归属与冲突（v1 的头号缺陷）

v1 打算写 `~/.config/systemd/user/ai-usage.service`——而 `deploy/install.sh` 装的
**正是同一路径同一文件名**。后果：老用户装过 install.sh 之后，面板会把别人的部署
误报成「本功能已开启」，关开关时还会把它删掉。这直接违反上面两条硬约束。

修法分三层。

### 1.1 独立命名

| 平台 | 本功能的 artifact | 已被占用、绝不能碰的 |
|---|---|---|
| Linux systemd | `~/.config/systemd/user/ai-usage-autostart.service` | `ai-usage.service`（install.sh） |
| Linux XDG | `$XDG_CONFIG_HOME/autostart/ai-usage-autostart.desktop` | — |
| macOS | `~/Library/LaunchAgents/io.github.ai-usage.autostart.plist` | — |
| Windows | `<Startup>\ai-usage-autostart.lnk` | — |

`$XDG_CONFIG_HOME` 未设置时才回落 `~/.config`，systemd 用户目录同理。

### 1.2 ownership marker

每个 artifact 内部必须带可机读的归属标记，格式版本 + 项目根路径：

- systemd `.service`：文件首部注释
  ```
  # ai-usage-autostart: 1
  # project-root: /home/x/ai-usage
  # 本文件由 ai-usage 面板的自启动开关生成，关闭开关即删除；手动删除也安全。
  ```
- XDG `.desktop`：`X-AIUsage-Autostart=1` + `X-AIUsage-ProjectRoot=...`（`X-` 是 desktop
  entry 规范的官方扩展前缀）
- macOS `.plist`：顶层 key `AIUsageAutostart = {version, projectRoot}`，用 `plistlib` 写
- Windows `.lnk`：写进快捷方式的 `Description` 字段（`ai-usage-autostart 1 root=<path>`），
  用同一个 COM 对象即可读回

**铁律：读不到自己的 marker 的文件，一律不覆盖、不删除。** 遇到就返回冲突态，
给出路径和手工处理说明，让用户自己决定。

### 1.3 与 install.sh 部署共存

`enable()` 之前先探测 `ai-usage.service`：

- 存在且 `systemctl --user is-enabled` 为 `enabled` → **不写任何文件**，返回
  `state = "external"`、`enabled = true`。界面把开关置为「已由 systemd 部署接管」并禁用，
  文案指向 `systemctl --user disable ai-usage.service`。
  这是如实汇报：那个 unit 确实实现了登录自启，用户的目的已经达到。
- 存在但未 enable → `state = "conflict"`，**拒绝开启**并说明两个 unit 会抢同一端口。
  用户可以自己删掉旧的再来开。

反向也要防：本功能已开启时，用户再跑 `install.sh`，会出现两个 unit 抢端口。
`install.sh` 里加一段检测，发现 `ai-usage-autostart.service` 就提示并退出。

---

## 二、为什么不碰 linger

v1 写的是「`enable()` 尝试 `loginctl enable-linger`，失败则提示手动执行」。三条理由整体删除：

1. **语义相反**。用户级 service 本来就会在首次登录时由 user manager 启动；linger 的作用是
   「**开机即启动，且最后一次注销后继续运行**」——恰恰是用户明确不要的常驻。
2. **无法保证恢复**。linger 是独立的系统状态。用户原本就开着 linger 时，关闭本功能不能
   把它关掉（会影响别的服务）；不关又留了残迹。「随时可取消」做不到。
3. **对阶段二有害**。linger 让服务在图形登录之前启动，拿不到 `DISPLAY`/Wayland 会话，
   窗口层必然失败。

`deploy/install.sh` 里现有的 linger 提示保持原样不动——那是「服务器式常驻」场景，
与本功能的「登录时按需」是两回事，不要混。

---

## 三、架构

新增 `server/autostart.py`。对外三个动作，调用方不感知平台：

```python
def status() -> AutostartStatus
def enable() -> AutostartStatus     # 返回操作后的状态，不返回 None
def disable() -> AutostartStatus
```

内部按**功能**而非按「有没有 systemd」分派 backend：daemon 归服务管理器，
GUI 层（阶段二）归各平台的登录机制。

```
server/autostart.py
├── detect_platform()   → windows | macos | linux | wsl | unsupported
├── SystemdBackend      Linux/WSL，且 systemctl --user 可用
├── XdgBackend          Linux，无 user systemd 时的回落
├── LaunchdBackend      macOS
└── WindowsBackend      Windows 启动文件夹（experimental，见 §7）
```

### 状态即事实

`status()` **不读任何自己写的布尔配置**，直接查操作系统里的实际情况。好处：用户手动删了
启动项，界面立刻如实反映，不存在「配置说开着、实际没开」的撒谎状态。

但 v1 把「文件存在」直接等同于「已启用」，这在三个平台上都不成立：

- macOS 13+ 允许在「系统设置 → 登录项」里关掉某个 agent，plist 还在
- Windows 允许在设置或任务管理器里禁用启动项，`.lnk` 还在
- XDG 的 `Hidden=true`、无效 `TryExec`、`OnlyShowIn/NotShowIn` 都会让文件存在却不运行

所以状态模型必须能表达「我不知道」：

```python
@dataclass
class AutostartStatus:
    supported: bool                    # 本平台是否有实现
    enabled: bool | None               # None = 查不出来，不是 False
    state: str                         # 见下表
    platform: str
    experimental: bool = False
    issues: list[str] = field(default_factory=list)
    detail: str | None = None          # 给界面直接显示的人话
```

| `state` | 含义 | `enabled` |
|---|---|---|
| `absent` | 没有本功能的 artifact | `False` |
| `owned_enabled` | 自己的 artifact，且系统确认会运行 | `True` |
| `owned_registered` | 自己的 artifact 在，但系统是否真会运行**查不到** | `None` |
| `owned_disabled` | 自己的 artifact 在，但已被系统/用户禁用 | `False` |
| `stale` | 自己的 artifact 在，但内容指向的路径已失效 | `False` |
| `external` | 检测到 install.sh 的部署（见 §1.3） | `True` |
| `conflict` | 有同名/同端口的非本功能 artifact，不能安全操作 | `None` |
| `query_failed` | 查询本身出错（命令超时等） | `None` |

`status()` **任何情况下不抛异常**。查询失败返回 `query_failed` 而不是 `supported=False`
——后者会让界面禁用开关，用户反而无法从 UI 清理残留项。

`stale` 的判定不能只看「解释器和项目目录还在不在」。必须**解析自己生成的 artifact 内容**，
逐项校验：格式版本可识别、`project-root` 目录存在、解释器可执行、`server/main.py` 存在。
任何一项不满足 → `stale`，界面提示「重新开关一次即可修复」。

---

## 四、生成的启动项长什么样

`enable()` 时把 `sys.executable` 与项目根目录**快照**进 artifact，不依赖 `uv`、不依赖 PATH
（服务管理器给的环境极精简，现有 `deploy/ai-usage.service` 已为此专门补过 `PATH`）。

**一律用平台原生序列化，禁止字符串拼 shell 命令**：plist 用 `plistlib`，`.desktop` 与
systemd unit 按各自的转义规则处理（含空格的路径必须正确引用）。

### Linux systemd

```ini
# ai-usage-autostart: 1
# project-root: /home/x/ai-usage
[Unit]
Description=ai-usage 面板（登录自启，由面板开关管理）
[Service]
Type=simple
WorkingDirectory=<project-root>
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=<sys.executable> -m server.main
Restart=on-failure
RestartSec=10
StartLimitIntervalSec=120
StartLimitBurst=3
[Install]
WantedBy=default.target
```

`StartLimitBurst` 是必要的：万一端口被别的东西占着，没有它就会无限重启刷日志。

写入后 `systemctl --user daemon-reload` + `systemctl --user enable`（**不加 `--now`**，
理由见 §5）。状态查询用 `systemctl --user is-enabled ai-usage-autostart.service`。

### macOS

`plistlib` 写 `Label` / `ProgramArguments=[sys.executable, "-m", "server.main"]` /
`WorkingDirectory` / `RunAtLoad=true` / `AIUsageAutostart` marker。
plist 权限设 `0o600`，**不得 group/world writable**（launchd 会拒绝加载）。

注册与注销**必须成对**——只删 plist 不会停掉已加载的 job：

- enable：写文件 → `launchctl bootstrap gui/$UID <plist>`
- disable：`launchctl bootout gui/$UID/<label>` → 删文件（bootout 报「不存在」视为成功）

状态：`launchctl print gui/$UID/<label>` 能查到即 `owned_enabled`；plist 在但查不到授权
状态 → `owned_registered`（`enabled=None`），**不得武断报 `True`**。

### Linux XDG（无 user systemd 时）

标准 `.desktop`，`Type=Application`、`Exec` 为引用正确的解释器命令、`X-AIUsage-*` marker。
状态判定必须读回文件并检查 `Hidden`、`TryExec`、`OnlyShowIn/NotShowIn`——命中任一
→ `owned_disabled`。

### WSL

走 SystemdBackend，但界面文案改成「**随 WSL 启动**」而不是「登录时启动」：WSL 实例本身
不随 Windows 开机，而是被 VSCode Remote-WSL 或 wsl 终端按需拉起，说「登录自启」是误导。

检测：`/proc/version` 含 `microsoft`，或存在 `WSL_DISTRO_NAME`。
user systemd 不可用（未设 `systemd=true`）→ `supported=False` + 明确的开启方法。

---

## 五、enable 只注册，不启动

`enable()` **不** `--now`、不立即 `launchctl kickstart`、不开第二个窗口。

理由很实在：用户是在**面板里**点这个开关的，说明 daemon 此刻正跑着、正占着端口。
立刻再拉一个只会撞端口失败。界面文案直说「下次登录生效」。

这同时消掉了 v1 的一整类失败路径（v1 的 macOS 分支写的是「enable 时顺带 load 使当前会话
立即生效」）。

---

## 六、原子性与失败处理

v1 承诺「要么干净开启，要么什么都没变」，但没有机制支撑。修法：

1. **序列化**。`enable`/`disable` 全程持一把进程内锁，拒绝并发。API 层并发 `PUT` 时
   第二个返回 409。
2. **原子替换**。临时文件写在**同一目录**再 `os.replace()`。目标路径是 symlink 时
   **直接拒绝**（不跟随），返回冲突。
3. **超时**。所有 `systemctl` / `launchctl` / `powershell` 调用带超时（10s），
   超时按失败处理并写进 `issues`。
4. **可回滚的先做**。顺序：校验冲突 → 写文件 → 注册（enable/bootstrap）。注册失败则
   **删掉刚写的文件**回到操作前状态。文件是自己刚写的、marker 可验证，删它是安全的。
5. **幂等**。重复 `enable` 对 owned artifact 是 no-op；对 external/conflict 报冲突不覆盖。
   `disable` 缺失项是 no-op；半成功（注销成功但删文件失败）保留状态让下次重试收尾。

**`disable` 后的验收断言是「恢复到操作前的快照」，不是「目录变空」。**
`~/.config/systemd/user`、`~/Library/LaunchAgents`、`autostart` 都是共享目录，
里面本来就有别人的东西。

---

## 七、Windows：做，但标 experimental

README 目前要求 Windows 用户把 daemon 跑在 WSL 里，项目从未在原生 Windows 上验证过。
按用户决定：**实现 Windows backend，但不做实机验证**，因此必须在文档与 UI 上如实标注
`experimental`，不得把未验证的路径包装成已支持。

代码层面已确认无 Unix 硬依赖：路径全走 `expanduser()`。已知风险一处：codex 若装成 `.cmd`
包装器，子进程可能起不来——但 Codex provider 有「读会话日志」的兜底降级，不会整页崩。

实现要点：

- 启动目录用 `FOLDERID_Startup` 解析（`SHGetKnownFolderPath`，经 `ctypes`），
  不硬编码 `%APPDATA%\Microsoft\...`——该路径可被重定向。
- **不用 VBScript**（微软已启动退役流程）。改用 `.lnk` 指向 `pythonw.exe`
  （无控制台窗口，天然不闪黑窗），参数 `-m server.main`，工作目录为项目根。
- `.lnk` 经 PowerShell 的 `WScript.Shell` COM 创建；marker 写进 `Description` 字段，
  同一 COM 对象可读回，用于归属校验与 `stale` 判定。
- 「文件存在」同样不等于会运行（用户可在设置/任务管理器里禁用）。查不到授权状态时
  返回 `owned_registered`（`enabled=None`），文案区分「已注册」与「系统确认会运行」。

---

## 八、API 与安全

这是整个设计里风险最高的部分：**一个本地 HTTP 端点获得了往用户系统写持久化启动项的能力**。
`127.0.0.1:8788` 不是只有本面板能访问——任何网页都能对本地端口发请求。

```
GET  /api/autostart                        → AutostartStatus
PUT  /api/autostart  {"enabled": bool}     → AutostartStatus
GET  /api/health                           → {"service": "ai-usage", "version": "..."}
```

`/api/health` 用于「占着这个端口的到底是不是 ai-usage」，阶段二依赖它；顺手在阶段一加上。
它是无副作用的只读端点，不套安全依赖。

### 8.1 点击劫持（v1 完全没防）

v1 的三重校验只防「恶意网页自己发跨域请求」。攻击者可以把**本地面板本身**放进透明
iframe，诱导用户点真开关——请求由面板自己发出，Host、Origin、自定义头**全部合法**，
v1 的每一道防线都会放行。

修法：**全局响应中间件**给所有响应加

```
Content-Security-Policy: frame-ancestors 'none'
X-Frame-Options: DENY
```

必须是 HTTP 响应头，`<meta>` 里的 CSP 不支持 `frame-ancestors`。`X-Frame-Options`
是给不支持 CSP L2 的老浏览器兜底。

再加一道：**首次开启需要二次确认**（界面上的确认步骤，不是 alert），
让「一次误点」不足以写入启动项。

### 8.2 `require_local_ui` 依赖

套在 `PUT /api/autostart` 和现有的 `POST /api/refresh` 上：

1. **Host 头**：必须精确匹配 `127.0.0.1:<port>`、`localhost:<port>` 或 `[::1]:<port>`
   （配置的 `ALLOWED_HOSTS` 已含 `::1`，v1 漏了 IPv6 的方括号形式）。
   缺失、重复、畸形一律拒绝。**不信任 `X-Forwarded-Host`。**
2. **自定义头**：要求 `X-Requested-By: ai-usage-panel`。这是主防线——跨域请求带自定义头
   必然触发预检，而服务端不注册任何 CORS 中间件，预检必然失败。浏览器**无法**绕过。
3. **Origin 头**：存在时必须匹配本机来源，显式拒绝 `null`；缺失时放行。

> ⚠️ 第 3 条的「缺失放行」是对评审意见的**有意保留**，请复审重点看这一条。
> 评审建议改为 fail-closed（缺失即拒），理由是 CLI 兜底可以直接调 backend、无需为 curl
> 放宽——这一点我接受，CLI 确实不走 HTTP。但另有一个理由：**Safari 在同源 POST/PUT 上
> 历史性地不发 `Origin`**，fail-closed 会让 Safari 用户的开关直接失灵，而这恰恰是
> macOS 用户的主力浏览器。既然第 2 条对浏览器已是不可绕过的硬门（跨域带自定义头必须
> 预检，预检必失败），第 3 条只是纵深防御，放宽它不降低实际安全性。
> 若复审仍认为应 fail-closed，改动很小，听复审的。

`POST /api/refresh` 的加固可以独立成一个提交，不必和自启动绑在一起。

---

## 九、界面

面板底部一个克制的设置区：默认收起，点齿轮展开。README 强调面板「只有一处是响的」
（节奏刻度），设置区不该抢戏。

- `supported=False`：开关禁用 + 说明原因
- `experimental=True`：开关旁一枚 experimental 标记
- `state` 为 `external` / `conflict`：开关禁用 + 指向对应的手工处理方法
- `enabled=None`：开关显示为不确定态，配一行「系统未告知是否会运行」，**不要**画成关闭
- `issues` 非空：开关下方一行黄字
- 操作失败：就地显示原因，不弹窗
- 首次开启：一步确认

---

## 十、测试

新增 `tests/test_autostart.py`，安全用例并入 `tests/test_api.py`。用 `monkeypatch` 假冒
HOME 与平台检测。评审明确指出「纯 monkeypatch 循环」不足以支撑公开的跨平台承诺，
以下用例是必需项：

**不碰系统**
- 关闭状态下反复调 `status()`，断言目标目录**零写入**（比对操作前后的目录快照）
- 目录中预置无关文件，走完 enable → disable，断言那些文件原样保留

**归属与冲突**
- 预置 `ai-usage.service`（install.sh 那份）→ 报 `external`，且**不写不删**任何文件
- 预置无 marker 的同名 artifact → 报 `conflict`，`enable`/`disable` 都不动它
- 目标路径是 symlink → 拒绝，不跟随
- marker 版本号无法识别 → 当作 foreign 处理

**状态**
- 各 backend 的 `absent → owned_* → absent` 全循环
- `stale`：解释器不存在 / 项目目录被移走 / `server/main.py` 缺失
- XDG `Hidden=true`、无效 `TryExec` → `owned_disabled`
- macOS plist 在但 `launchctl print` 查不到 → `owned_registered` 且 `enabled is None`
- 查询命令超时 → `query_failed`，且不抛异常

**失败与回滚**
- 注册步骤失败 → 刚写的文件被删除，回到操作前快照
- `disable` 半成功（注销成功、删文件失败）→ 再调一次能收尾
- 重复 `enable` 幂等；未 enable 时 `disable` 不报错

**API 安全**
- Host：合法三种（含 `[::1]:port`）通过；缺失 / 重复 / 畸形 / 外部域名拒绝；
  `X-Forwarded-Host` 不影响判定
- 缺 `X-Requested-By` → 拒绝
- `Origin` 匹配通过、不匹配拒绝、`null` 拒绝、缺失放行
- 预检（`OPTIONS`）拿不到任何 CORS 头
- 所有响应含 `frame-ancestors 'none'` 与 `X-Frame-Options: DENY`
- 并发 `PUT` → 一个成功一个 409
- `/api/refresh` 加固后原有行为不回归

---

## 十一、卸载

- README 增加一节：三平台的 artifact 路径、marker 长什么样、如何手工删除
- CLI 兜底：`python -m server.autostart status|disable`——面板打不开时仍能关掉自启动。
  这条路径不经过 HTTP，因此不受 §8 的任何校验影响。

---

## 十二、阶段二的待解问题（不在本文定稿）

登录后自动打开面板窗口，已知必须先解决：

1. **GUI 会话可用性**。服务管理器启动时可能还没有 `DISPLAY`/Wayland/登录会话。Linux 上
   窗口层应走 XDG autostart（图形登录后才跑）而非 systemd；macOS/Windows 用各自的
   GUI 登录机制。
2. **launcher 的生命周期契约**必须写明：是 `exec` 掉自己、常驻监督、还是拉起子进程后退出
   ——对 systemd/launchd/Startup 三者语义完全不同。
3. **别把别的服务当成 ai-usage**。只看「端口开着」会误判，必须打 `/api/health` 确认；
   探测与启动之间的竞态用进程锁处理。
4. **浏览器枚举**。v1 的 Chrome → Edge → Chromium → 默认 的链条建议大幅简化。
5. **重复窗口**。用户已经开着面板时不该再弹一个。

---

## 评审项处置对照

| # | 评审意见 | 处置 |
|---|---|---|
| 1 | 点击劫持绕过全部防线 | §8.1，CSP `frame-ancestors 'none'` + `X-Frame-Options` + 首次二次确认 |
| 2 | unit 撞名会误伤既有部署 | §1 全节，独立命名 + marker + `external`/`conflict` 状态 + install.sh 反向检测 |
| 3 | linger 语义相反 | §2，整体删除 |
| 4 | macOS 状态与注销 | §4，bootstrap/bootout 成对 + `owned_registered` + 0600 权限 |
| 5 | Windows VBS/路径/状态 | §7，`FOLDERID_Startup` + `.lnk`→`pythonw.exe` + 区分已注册/确认运行 |
| 6 | Windows 未验证 | §7，实现但标 experimental，UI 与文档均标 |
| 7 | XDG 细节与 backend 划分 | §3/§4，按功能分派；`XDG_CONFIG_HOME` 优先；检查 `Hidden`/`TryExec`/`*ShowIn` |
| 8 | launcher 生命周期缺失 | 阶段一无 launcher（OS 直接 ExecStart），整类问题消失；阶段二 §12 列为前置 |
| 9 | 状态模型混淆 | §3，`enabled: bool\|None` + `supported` + 八态 `state` + `query_failed` |
| 10 | 失败处理无法保证原子 | §6 全节；测试断言改为「恢复到操作前快照」 |
| 11 | 路径快照校验不足 | §3 末 + §4，解析自己的 artifact 内容逐项校验；平台原生序列化，禁止拼 shell |
| 12 | 安全细节收紧 | §8.2，补 `[::1]:port`、拒绝重复/畸形、不信 `X-Forwarded-Host`；**Origin 缺失放行有意保留，附理由请复审** |
| 13 | 测试不足 | §10，按评审列举逐条补齐 |
| 14 | 范围建议拆两阶段 | 采纳，见〈范围〉；linger、立即 load、浏览器枚举、`no_browser` 历史状态均已砍；`/api/refresh` 加固独立提交 |
