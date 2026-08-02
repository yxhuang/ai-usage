# 自启动开关设计（v3）

2026-08-02

> **修订史**
>
> - v1 → Codex 评审判「需修改后开工」：3 阻断 + 10 重要 + 1 范围。
> - v2 按那 14 项整体重写，拆两阶段。复审仍判「需修改后开工」：5 项真正解决、9 项部分解决，
>   新发现 3 项阻断——其中两项是 v2 自己引入的**内部矛盾**（见下）。
> - v3 = 本文。逐项处置见文末对照表。
>
> **v2 的两处自相矛盾（v3 已修）**：
> 1. §5 承诺「enable 只注册不启动」，§4 macOS 却写 `launchctl bootstrap` + `RunAtLoad=true`
>    ——两者合起来就是立即启动，会撞上正在处理该请求的 daemon。
> 2. §3 说 `stale` 可「重新开关一次修复」，§6 却规定「重复 enable 对 owned artifact 是 no-op」
>    ——`stale` 和 `owned_disabled` 因此永远修不好。
>
> 前置依赖已完成：公版默认值改造（`2026-08-02-public-defaults-design.md`，`da153ff`）。

## 目标

面板里加一个可开关的「登录时自动启动」选项，跨 Windows / macOS / Linux 通用。

硬约束，来自用户，优先级高于任何功能考量：

- **不开启就不碰用户系统**。默认关闭；只有显式点开关才写文件。
- **随时可取消**，恢复到操作前的状态。
- **绝不误伤不属于本功能的东西**。

## 范围：分两阶段，本文只定阶段一

| 阶段 | 内容 | 状态 |
|---|---|---|
| **一** | 登录时自动启动 **daemon** | 本文定义，可开工 |
| 二 | 登录后自动**打开面板窗口** | 只列待解问题，见 §12 |

拆分理由：阶段一由 OS 的启动机制直接拉起 daemon，不需要中间 launcher，v1 里
「launcher 生命周期未定义」「端口探测误判」「探测与启动竞态」三类问题整体消失。
阶段二真正的难点（GUI 会话可用性、浏览器枚举、重复窗口）与阶段一无耦合。

对本仓库作者（WSL）两阶段都不改变现状：daemon 已由 `deploy/install.sh` 的 systemd unit
管着，窗口已由 `~/.vscode-server/server-env-setup` → `ai-usage-panel` → `launcher.vbs` 拉起。
本功能面向外部用户。

## 非目标

- 跨平台托盘图标（`deploy/tray-widget.ps1` 保留为 Windows 侧可选增强，不纳入本功能）
- 把开关状态写进 `config.toml`（见〈状态即事实〉）
- 启动延迟、条件启动
- linger。**整体删除**，理由见 §2

---

## 一、归属与冲突

`deploy/install.sh` 装的是 `~/.config/systemd/user/ai-usage.service`。v1 打算写同一路径同一
文件名，会把别人的部署误报成本功能、并在关开关时删掉它。v2 改了名字，但仅凭「文件名叫
`ai-usage.service` 且 enabled」就断定是 install.sh 部署——不同 checkout、同名的第三方 unit、
带 drop-in 的 unit 都会被误判。v3 改成按**内容**判定。

### 1.1 独立命名

| 平台 | 本功能的 artifact |
|---|---|
| Linux systemd | `~/.config/systemd/user/ai-usage-autostart.service` |
| Linux XDG | `$XDG_CONFIG_HOME/autostart/ai-usage-autostart.desktop` |
| macOS | `~/Library/LaunchAgents/io.github.ai-usage.autostart.plist` |
| Windows | `<FOLDERID_Startup>\ai-usage-autostart.lnk` |

`$XDG_CONFIG_HOME` 未设置时才回落 `~/.config`；systemd 用户目录同理。

### 1.2 ownership marker

每个 artifact 内嵌可机读标记：格式版本 + 规范化项目根路径。

- systemd `.service`：首部注释 `# ai-usage-autostart: 1` / `# project-root: <abs>`
- XDG `.desktop`：`X-AIUsage-Autostart=1` / `X-AIUsage-ProjectRoot=<abs>`（`X-` 是 desktop
  entry 规范的官方扩展前缀）
- macOS `.plist`：顶层 key `AIUsageAutostart = {version, projectRoot}`，`plistlib` 写
- Windows `.lnk`：写进 `Description` 属性，同一 COM 对象可读回

**判定为 `owned` 必须同时满足三条**，缺一即视为 foreign：

1. marker 存在且版本号可识别
2. marker 里的 project-root 规范化后**等于当前进程的项目根**
3. artifact 里的可执行目标 / 参数 / 工作目录与本功能会生成的一致

不同项目根的同名 artifact 是 `foreign`，不是 owned——同一台机器上两份 checkout 是常见情况。

**铁律：非 owned 的文件一律不覆盖、不删除。** 返回冲突并给出路径与手工处理说明。

### 1.3 统一盘点，不只查当前 backend

每次 `status()` / `enable()` / `disable()` 都**同时盘点所有已知 artifact**，而不是只查当前
平台选中的那一个。原因：用户可能从有 systemd 的机器切到没有的（或反之），backend 变化后会
同时留下两种启动项，两个都想绑同一个端口。

盘点清单（Linux/WSL）：旧 `ai-usage.service`、新 `ai-usage-autostart.service`、
XDG `ai-usage-autostart.desktop`。

### 1.4 识别 install.sh 的既有部署

**不看文件名，看 systemd 的有效配置**：

```
systemctl --user show ai-usage.service \
  --property=FragmentPath,DropInPaths,ExecStart,WorkingDirectory,UnitFileState
```

`WorkingDirectory` 规范化后等于当前项目根、且 `ExecStart` 指向本项目的 `server.main`
→ 认定为本项目的 legacy 部署。否则一律当 foreign，只报冲突不下结论。

`UnitFileState` 不是只有 `enabled`/`disabled` 两种，必须逐项映射：
`enabled` / `enabled-runtime` → 会自启；`disabled` / `static` / `linked` → 不会；
`masked` → 不会且需要先 unmask；未知值 → `unknown`，不猜。

认定为 legacy 部署且会自启时：**不写任何文件**，返回 `managed_by = "legacy_deploy"`、
`enabled = true`。界面把开关置灰并说明「已由 `deploy/install.sh` 的部署接管」。
这是如实汇报——那个 unit 确实实现了登录自启，用户的目的已达到。

文案必须给**完整**迁移步骤，不能只说 `systemctl --user disable`（那样 unit 文件还在，
下次查询会变成冲突）：

```
systemctl --user disable --now ai-usage.service
rm ~/.config/systemd/user/ai-usage.service
systemctl --user daemon-reload
```

### 1.5 反向保护

`install.sh` 里加检测：发现 `ai-usage-autostart.service` **或** XDG 的
`ai-usage-autostart.desktop` 就提示并退出。这段检测必须放在**任何写操作之前**
（现有脚本会先 `uv sync` 建 `.venv`，要放在它前面）。

同时给 `deploy/ai-usage.service` 模板加一行部署 marker 注释——对存量部署无效，
所以 §1.4 的检测仍以 `systemctl show` 为准，marker 只是给以后的部署多一层确认。

---

## 二、为什么不碰 linger

1. **语义相反**。用户级 service 本来就会在首次登录时由 user manager 启动；linger 的作用是
   「开机即启动，且最后一次注销后继续运行」——恰恰是用户明确不要的常驻。
2. **无法保证恢复**。linger 是独立的系统状态。用户原本就开着 linger 时，关闭本功能不能把它
   关掉（会影响别的服务），不关又留残迹。「随时可取消」做不到。
3. **对阶段二有害**。linger 让服务在图形登录之前启动，拿不到 `DISPLAY`/Wayland 会话。

`deploy/install.sh` 现有的 linger 提示保持原样——那是「服务器式常驻」场景，与本功能的
「登录时按需」是两回事。

---

## 三、状态模型

`status()` **不读任何自己写的布尔配置**，直接查操作系统里的实际情况。用户手动删了启动项，
界面立刻如实反映，不存在「配置说开着、实际没开」的撒谎状态。

但「文件存在」不等于「会运行」：macOS 13+ 可在「登录项」里关掉而保留 plist；Windows 可在
设置/任务管理器里禁用而保留 `.lnk`；XDG 的 `Hidden=true`、无效 `TryExec`、
`OnlyShowIn/NotShowIn` 都会让文件存在却不运行。

v2 把这些塞进一个八值枚举，结果既不完备也不互斥（owned 与 external 可以同时存在，
查询失败与 owned artifact 也可以同时存在）。v3 拆成**正交维度**：

```python
@dataclass
class AutostartStatus:
    supported: bool                  # 本平台是否有实现
    experimental: bool               # 是否未经实机验证（Windows）
    platform: str
    enabled: bool | None             # 登录时是否会自动启动 ai-usage（任何机制）；None = 查不出
    managed_by: str                  # panel | legacy_deploy | other | none | unknown
    artifact: str                    # absent | owned | owned_stale | foreign
    registration: str                # registered | not_registered | disabled_by_system | unknown
    conflicts: list[Conflict]        # {kind, path, detail}，可为多条
    query_error: str | None
    issues: list[str]
    detail: str | None               # 给界面直接显示的人话
```

`enabled` 回答的是用户真正关心的问题——「下次登录它会不会自己起来」——所以 legacy 部署
也算 `true`；`managed_by` 才说明是谁在管。`enabled` 由其余字段推导，不单独存储：

- `artifact == owned` 且 `registration == registered` → `True`
- `managed_by == legacy_deploy` 且其 `UnitFileState` 会自启 → `True`
- `registration == unknown` 或 `query_error` 挡住了判定 → `None`
- 其余 → `False`

`status()` **任何情况下不抛异常**。查询失败填 `query_error` 并把能查到的维度照常返回，
**不得**降级成 `supported=False`——后者会让界面禁用开关，用户反而无法从 UI 清理残留项。

### `owned_stale` 的判定

必须**解析自己生成的 artifact 内容**逐项校验，不能只看「解释器和目录还在不在」：

1. 格式版本可识别
2. `project-root` 目录存在，且 `server/main.py` 存在
3. 记录的解释器可执行
4. 用该解释器实跑一次轻量自检（`-c "import server.main"` + 加载配置），确认依赖可导入、
   配置可解析。**只检查文件存在证明不了能启动**——服务管理器给的环境极精简。

任一项不满足 → `owned_stale`。

### 状态 × 动作转移表

v2 的「重复 enable 对 owned artifact 一律 no-op」是错的，它让 `owned_stale` 和
`disabled_by_system` 永远修不好。正确规则：**覆盖自己的 artifact 是安全的**（marker 已证明
归属），no-op 只适用于「已经正确开着」。

| artifact | registration | `enable()` | `disable()` |
|---|---|---|---|
| `absent` | — | 写入 + 注册 | no-op |
| `owned` | `registered` | no-op | 注销 + 删除 |
| `owned` | `not_registered` | **重新注册** | 删除 |
| `owned` | `disabled_by_system` | **重写 + 重新注册**，仍被系统禁用则写 `issues` 并如实报 | 注销 + 删除 |
| `owned_stale` | 任意 | **重写 + 重新注册**（修复路径） | 注销 + 删除 |
| `foreign` | 任意 | 拒绝，返回冲突 | 拒绝，不动它 |
| — | — | `managed_by == legacy_deploy` 时拒绝，给 §1.4 的迁移步骤 | 同左 |

`disabled_by_system`（macOS 登录项开关、Windows 任务管理器）是**系统级用户意图**，
重写 artifact 不一定能解除。所以上表要求「重写后如实复查」，不能假装修好了。

---

## 四、生成的启动项

`enable()` 把 `sys.executable` 与规范化的项目根**快照**进 artifact，不依赖 `uv`、不依赖 PATH。

**必须一并快照 `AI_USAGE_CONFIG`**（`server/config.py` 支持该环境变量）。用自定义配置
——可能是不同端口——跑着面板的人开了自启，下次登录若静默换回仓库默认配置，行为就变了。
当前进程有该变量时写进 artifact 的环境；它指向的文件不存在时拒绝 enable 并说明原因。

**一律用平台原生序列化，禁止字符串拼 shell 命令**：plist 用 `plistlib`；`.desktop` 与
systemd unit 按各自转义规则处理（含空格、`%` 的路径必须正确转义）。

### Linux systemd

```ini
# ai-usage-autostart: 1
# project-root: /home/x/ai-usage
[Unit]
Description=ai-usage 面板（登录自启，由面板开关管理）
StartLimitIntervalSec=120
StartLimitBurst=3

[Service]
Type=simple
WorkingDirectory=<project-root>
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
# 仅当前进程有 AI_USAGE_CONFIG 时才写这一行
Environment=AI_USAGE_CONFIG=<abs>
ExecStart=<sys.executable> -m server.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

⚠️ `StartLimitIntervalSec` / `StartLimitBurst` 属于 `[Unit]`（systemd v229 起）。
v2 把它们写在 `[Service]` 里，现代 systemd 会当未知键忽略——限流就失效了。
测试须对生成的文件跑 `systemd-analyze verify`。

写入后 `systemctl --user daemon-reload` + `systemctl --user enable`，**不加 `--now`**（§5）。

### macOS

`plistlib` 写 `Label` / `ProgramArguments=[sys.executable, "-m", "server.main"]` /
`WorkingDirectory` / `EnvironmentVariables`（含 PATH 与可选的 `AI_USAGE_CONFIG`）/
`RunAtLoad=true` / `AIUsageAutostart` marker。plist 权限 `0o600`，
**不得 group/world writable**（launchd 会拒绝加载）。

**enable 不做 `launchctl bootstrap`**——`bootstrap` 会把 job 载入当前 GUI domain，
配合 `RunAtLoad=true` 会立刻拉起第二个 daemon，撞上正在处理这个 HTTP 请求的实例。
`~/Library/LaunchAgents` 本来就在登录时被加载，写文件已经足够。（这是 v2 的自相矛盾之一。）

**disable 只删 plist，不做 `bootout`**。若当前 daemon 正是 launchd 在本次登录时启动的，
`bootout` 会在响应返回之前把自己杀掉。删掉 plist 后当前实例继续运行到注销，下次登录不再启动
——与 enable 的「只影响下次登录」对称，也更诚实。界面文案说明这一点。

状态查询：`launchctl print gui/$UID/<label>`。查得到 → `registered`；plist 在但查不到授权
状态 → `registration = "unknown"`（`enabled = None`），**不得武断报 `True`**。

### Linux XDG（无 user systemd 时的降级路径）

**必须如实说明它不是服务管理器**：XDG autostart 只在图形登录时把命令跑一次，
**不监督、不自动重启、daemon 崩了不会拉起来**。因此：

- 标记 `experimental`，`issues` 里带一条说明
- 纯文字终端登录（无图形会话）时它根本不会跑
- 界面文案区分：systemd 路径写「登录时自动启动并在崩溃后重启」，XDG 路径只写「图形登录时启动」

`.desktop` 写 `Type=Application` / `Exec`（正确引用）/ `X-AIUsage-*` marker。

状态判定读回文件并检查：`Hidden=true` → `disabled_by_system`；`TryExec` 指向不存在的可执行
→ `disabled_by_system`；`OnlyShowIn`/`NotShowIn` **必须结合 `$XDG_CURRENT_DESKTOP` 实际计算**
（见到字段就判 disabled 是错的）。

### WSL

走 systemd 路径，但界面文案改成「**随 WSL 启动**」而不是「登录时启动」：WSL 实例本身不随
Windows 开机，而是被 VSCode Remote-WSL 或 wsl 终端按需拉起，说「登录自启」是误导。

检测：`/proc/version` 含 `microsoft`，或存在 `WSL_DISTRO_NAME`。
user systemd 不可用（未设 `systemd=true`）→ `supported=False` + 明确的开启方法。

---

## 五、enable 只影响下次登录

`enable()` 不 `--now`、不 `bootstrap`、不 `kickstart`、不开第二个窗口。
`disable()` 同样只改下次登录，不去杀当前进程。

理由：用户是在**面板里**点这个开关的，daemon 此刻正跑着、正占着端口。立刻再拉一个只会撞端口
失败；反过来立刻杀掉，会杀死正在返回这个响应的进程。界面文案直说「下次登录生效」。

`owned` 且 `registered` 的 artifact 已经存在时重复 enable 是 no-op（见 §3 转移表），
因此这条规则不会掩盖「需要修复」的情形。

---

## 六、并发、失败与恢复

v2 承诺「要么干净开启，要么什么都没变」。跨文件系统 + 服务管理器 + GUI 注册的多步操作
**无法**给出真正的事务保证，v2 自己也留了「注销成功、删文件失败」的半成功口子。
v3 把承诺改成能兑现的说法。

### 6.1 跨进程锁

进程内锁挡不住「CLI 与 API 同时操作」「两个 checkout 各跑一个 daemon」。改用**跨进程锁**：
Unix 用 `fcntl.flock` 锁一个固定路径的锁文件，Windows 用具名互斥体。
拿不到锁 → API 返回 409，CLI 打印占用提示。

### 6.2 前向步骤与逆序补偿

每个 backend 写死一张「前向步骤 → 逆序补偿」表，失败时**按逆序**执行补偿：

| backend | 前向 | 补偿 |
|---|---|---|
| systemd | ① 备份原文件（若 owned 且存在）② 原子写入 ③ `daemon-reload` ④ `enable` | ④′ `disable` ③′ `daemon-reload` ②′ 恢复备份 / 删除新文件 |
| launchd | ① 备份 ② 原子写入（0600） | ②′ 恢复备份 / 删除 |
| XDG | ① 备份 ② 原子写入 | ②′ 恢复备份 / 删除 |
| Windows | ① 备份 ② 写临时 `.lnk` ③ 读回校验 ④ `os.replace` | ④′ 恢复备份 / 删除 |

要点：

- **已有 owned artifact 更新失败时恢复原文件，不能直接删除**（那会把用户本来好好的自启动
  弄没了）。
- 原子替换：临时文件写在**同一目录**再 `os.replace()`。目标是 symlink 时**直接拒绝**，
  不跟随。
- 记录本次操作**新建的目录**，补偿时只删自己新建的。
- 所有 `systemctl` / `launchctl` / `powershell` 调用带 10s 超时，超时按失败处理。

### 6.3 承诺降级为「尽力恢复 + 如实上报」

补偿本身也可能失败。此时**不撒谎**：返回 `recovery_required`，`detail` 里给出精确的手工修复
命令（具体到路径与命令行）。这比一个兑现不了的绝对承诺诚实。

`disable` 半成功同理：保留可重试的状态，下次调用能收尾。

### 6.4 验收断言

**`disable` 后的断言是「恢复到操作前的目录快照」，不是「目录变空」。**
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

- 启动目录用 `SHGetKnownFolderPath(FOLDERID_Startup)` 经 `ctypes` 解析，**不硬编码**
  `%APPDATA%\Microsoft\...`（该路径可被重定向）。注意 COM 初始化与 `CoTaskMemFree`。
- **不用 VBScript**（微软已启动退役流程）。用 `.lnk` 指向 `pythonw.exe`（无控制台窗口），
  参数 `-m server.main`，工作目录为项目根。
- `pythonw.exe` 的解析必须写明：取 `sys.executable` 同目录下的 `pythonw.exe`；
  **不存在时不要猜**，返回 `supported=False` + 原因（venv 布局各异，猜错就是静默失败）。
- `.lnk` 经 PowerShell 的 `WScript.Shell` COM 创建。**禁止把项目路径插值进 `-Command`
  字符串**——用参数数组或固定脚本 + 安全传参，否则含引号/`$` 的路径就是命令注入。
- 先写临时 `.lnk` → **读回校验** Description marker / TargetPath / Arguments /
  WorkingDirectory → 再 `os.replace` 到启动目录。
- 「文件存在」不等于会运行（用户可在设置/任务管理器里禁用）。查不到授权状态时
  `registration = "unknown"`，文案区分「已登记」与「系统确认会运行」。

---

## 八、API 与安全

风险最高的部分：**一个本地 HTTP 端点获得了往用户系统写持久化启动项的能力**。
`127.0.0.1:8788` 不是只有本面板能访问——任何网页都能对本地端口发请求。

```
GET  /api/autostart                        → AutostartStatus
PUT  /api/autostart  {"enabled": bool}     → AutostartStatus
GET  /api/health                           → {"service": "ai-usage", "version": "..."}
```

`/api/health` 用于「占着这个端口的到底是不是 ai-usage」，阶段二依赖它；顺手在阶段一加上。
它无副作用，不套安全依赖。

### 8.1 点击劫持

v1/v2 之前的三重校验只防「恶意网页自己发跨域请求」。攻击者可以把**本地面板本身**放进透明
iframe 诱导用户点真开关——请求由面板自己发出，Host、Origin、自定义头全部合法。

修法：**全局响应中间件**给所有响应加

```
Content-Security-Policy: frame-ancestors 'none'
X-Frame-Options: DENY
```

必须是 HTTP 响应头，`<meta>` 里的 CSP 不支持 `frame-ancestors`；`X-Frame-Options` 给老浏览器
兜底。再加一道：**首次开启需要界面上的二次确认**，让一次误点不足以写入启动项。

### 8.2 `require_local_ui` 依赖

套在 `PUT /api/autostart` 和现有的 `POST /api/refresh` 上：

1. **Host 头**：必须精确匹配 `127.0.0.1:<port>`、`localhost:<port>` 或 `[::1]:<port>`。
   缺失、重复、畸形一律拒绝。**不信任 `X-Forwarded-Host`。**
2. **自定义头**：要求 `X-Requested-By: ai-usage-panel`。跨域携带非 safelisted 自定义头必然
   触发预检，而服务端不注册任何 CORS 中间件，预检必然失败——浏览器无法绕过。
3. **Origin 头**：**fail-closed**。必须存在，且精确等于由 Host 推导出的
   `http://<host>:<port>`；缺失、`null`、畸形、不匹配一律 403。

> v2 曾以「Safari 同源 POST 不发 Origin」为由放行缺失的 Origin。**这个理由不成立**：
> Fetch 规范要求非 GET/HEAD 请求携带 Origin，WebKit 早在 2008 年（bug 20792）就修了那个
> 老问题。CLI 兜底走 `python -m server.autostart`，根本不经过 HTTP，没有为非浏览器工具放宽
> 的需求。已改为 fail-closed。

`POST /api/refresh` 的加固可以独立成一个提交，不必和自启动绑在一起。

---

## 九、界面

面板底部一个克制的设置区：默认收起，点齿轮展开。README 强调面板「只有一处是响的」
（节奏刻度），设置区不该抢戏。

- `supported=False`：开关禁用 + 原因
- `experimental=True`：开关旁一枚 experimental 标记
- `managed_by != "panel"` 且非 `none`：开关禁用 + §1.4 的完整迁移步骤
- `conflicts` 非空：开关禁用 + 列出冲突路径与手工处理方法
- `enabled=None`：不确定态，配一行「系统未告知是否会运行」，**不要**画成关闭
- `artifact == "owned_stale"`：提示「点一下开关即可修复」（转移表保证这条真的有效）
- 措辞不夸大：systemd `enabled` 只证明**已登记**，不证明下次一定启动成功。
  文案用「已登记，下次登录将尝试启动」，不用「系统确认会运行」。
- 操作失败：就地显示原因，不弹窗；`recovery_required` 时显示手工修复命令
- 首次开启：一步确认

---

## 十、测试

新增 `tests/test_autostart.py`，安全用例并入 `tests/test_api.py`。用 `monkeypatch` 假冒 HOME
与平台检测。

**不碰系统**
- 关闭状态下反复 `status()`，断言目标目录零写入（比对前后快照）
- 目录中预置无关文件，走完 enable → disable，断言原样保留

**归属与冲突**
- 预置 install.sh 那份 unit（含 `systemctl show` 输出的桩）→ 认定 `legacy_deploy`，不写不删
- 同名但 `WorkingDirectory` 指向**另一个 checkout** → `foreign`，拒绝操作
- marker 版本号无法识别 / project-root 不匹配 → `foreign`
- systemd 与 XDG artifact **同时存在** → 盘点到两条冲突
- 目标路径是 symlink → 拒绝，不跟随
- `UnitFileState` 为 `masked` / `static` / `linked` / 未知值的逐项映射
- `install.sh` 在**任何写操作之前**就拦下两类 artifact

**状态与转移**
- 各 backend 的 `absent → owned → absent` 全循环
- `owned_stale`：解释器不存在 / 项目目录被移走 / 依赖导入失败 / 配置解析失败
- `owned_stale → enable` 与 `disabled_by_system → enable` 确实执行重写（**不是 no-op**）
- XDG `Hidden=true`、无效 `TryExec` → `disabled_by_system`；
  `OnlyShowIn`/`NotShowIn` 结合 `$XDG_CURRENT_DESKTOP` 计算，两种结果都要测
- macOS plist 在但 `launchctl print` 查不到 → `registration == "unknown"` 且 `enabled is None`
- 查询命令超时 → `query_error` 有值、不抛异常、其余维度照常返回

**失败与补偿**
- 逐步故障注入：写入失败 / `daemon-reload` 失败 / `enable` 失败，各自按逆序补偿到操作前快照
- 已有 owned artifact 更新失败 → **恢复原文件**，不是删除
- 补偿本身失败 → `recovery_required` + `detail` 含具体修复命令
- `disable` 半成功后再调一次能收尾
- 跨进程锁：CLI 与 API 并发 → 一方 409/占用提示

**生成物正确性**
- 生成的 unit 过 `systemd-analyze verify`
- 含空格、`%`、引号的路径在三种格式里都正确转义
- `AI_USAGE_CONFIG` 存在时被快照进 artifact；指向的文件不存在时拒绝 enable

**API 安全**
- Host：合法三种（含 `[::1]:port`）通过；缺失/重复/畸形/外部域名拒绝；
  `X-Forwarded-Host` 不影响判定
- 缺 `X-Requested-By` → 403
- Origin：匹配通过；缺失 / `null` / 畸形 / 不匹配一律 403
- `OPTIONS` 预检拿不到任何 CORS 头（这正是决定浏览器行为的服务端事实，
  比起真跑一个浏览器集成测试，这条断言等价且可维护）
- 所有响应含 `frame-ancestors 'none'` 与 `X-Frame-Options: DENY`
- 并发 `PUT` → 一个成功一个 409
- `/api/refresh` 加固后原有行为不回归

---

## 十一、卸载

- README 增加一节：三平台的 artifact 路径、marker 长什么样、如何手工删除
- CLI 兜底：`python -m server.autostart status|disable`——面板打不开时仍能关掉自启动。
  这条路径不经过 HTTP，不受 §8 校验影响，但**同样要拿 §6.1 的跨进程锁**。

---

## 十二、阶段二的待解问题（不在本文定稿）

1. **GUI 会话可用性**。启动时可能还没有 `DISPLAY`/Wayland/登录会话。Linux 上窗口层应走
   XDG autostart（图形登录后才跑）而非 systemd；macOS/Windows 用各自的 GUI 登录机制。
2. **launcher 生命周期契约**必须写明：`exec` 掉自己、常驻监督、还是拉起子进程后退出
   ——对三个平台语义完全不同。
3. **别把别的服务当成 ai-usage**：只看端口开着会误判，必须打 `/api/health`；探测与启动之间
   的竞态用锁处理。
4. **浏览器枚举**大幅简化。
5. **重复窗口**：用户已经开着面板时不该再弹一个。

---

## 评审项处置对照

### v1 的 14 项（复审判定 5 项真正解决、9 项部分解决）

| # | v1 意见 | v3 位置 | 复审对 v2 的判定 → v3 补了什么 |
|---|---|---|---|
| 1 | 点击劫持 | §8.1 | 真正解决，v3 无改动 |
| 2 | unit 撞名 | §1 全节 | 部分 → 改按 `systemctl show` 内容判定；owned 三条件含项目根；统一盘点；install.sh 前置检测 |
| 3 | linger | §2 | 真正解决 |
| 4 | macOS 状态与注销 | §4 | 部分 → 去掉 `bootstrap`/`bootout`，与 §5 一致 |
| 5 | Windows 机制 | §7 | 部分 → 补 `pythonw.exe` 解析契约、PowerShell 安全传参、写后读回校验 |
| 6 | Windows 未验证 | §7 | 真正解决 |
| 7 | XDG 与 backend 划分 | §4 | 部分 → 明说 XDG 不监督不重启、标 experimental；`*ShowIn` 结合当前桌面计算 |
| 8 | launcher 生命周期 | §12 | 真正解决（阶段一无 launcher） |
| 9 | 状态模型 | §3 | 部分 → 八值枚举拆成正交维度 + 完整转移表 |
| 10 | 原子性 | §6 | 部分 → 跨进程锁、逐 backend 补偿表、承诺降级为 `recovery_required` |
| 11 | 路径快照 | §3/§4 | 部分 → 加依赖与配置实测；快照 `AI_USAGE_CONFIG` |
| 12 | 安全细节 | §8.2 | 部分 → Origin 改 fail-closed（v2 的 Safari 理由已证伪） |
| 13 | 测试 | §10 | 部分 → 按复审清单补齐 |
| 14 | 拆两阶段 | 〈范围〉 | 真正解决 |

### 复审新提的 3 项阻断 + 6 项重要

| 意见 | 处置 |
|---|---|
| 阻断 1 归属未闭环 | §1.3/§1.4/§1.5：统一盘点、按内容判定、install.sh 写前拦截 |
| 阻断 2 macOS 自相矛盾 | §4 macOS：enable 不 bootstrap、disable 不 bootout |
| 阻断 3 §6 兑现不了 | §6：跨进程锁 + 补偿表 + `recovery_required` |
| 重要 1 八态非状态机 | §3：正交维度 + 转移表，顺带修好 `stale` 永远修不好的 bug |
| 重要 2 XDG 与承诺不符 | §4 XDG：如实标注不监督、experimental |
| 重要 3 StartLimit 放错 | §4：移入 `[Unit]`，测试加 `systemd-analyze verify` |
| 重要 4 漏 `AI_USAGE_CONFIG` | §4 开头 |
| 重要 5 Windows 细节 | §7 |
| 重要 6 测试覆盖 | §10 |
| 次要 `UnitFileState` 多态 | §1.4 |
| 次要 `external` 文案不完整 | §1.4 给完整迁移三步 |
| 次要 `owned_enabled` 表述过强 | §9 措辞规则 |

**一处未照办**：复审要求「真实浏览器侧预检不发送实际 PUT 的集成测试」。本项目没有浏览器
测试基础设施，为一条断言引入整套 e2e 不成比例。改为断言服务端在 `OPTIONS` 上不返回任何
CORS 头——决定浏览器是否发出实际请求的正是这个服务端事实，断言等价且可维护。
