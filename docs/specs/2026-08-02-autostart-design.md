# 自启动开关设计（v4）

2026-08-02

> **修订史**
>
> | 版本 | 评审结论 | 主要问题 |
> |---|---|---|
> | v1 | 需修改后开工 | 3 阻断 + 10 重要 + 1 范围 |
> | v2 | 需修改后开工 | 5 项真正解决；新增 3 阻断，其中 2 项是 v2 自己引入的**内部矛盾** |
> | v3 | 需修改后开工 | 4 阻断：归属认定、状态模型、补偿顺序、配置快照在半数平台无处可放 |
> | v4 | 本文 | 见文末〈评审项处置对照〉 |
>
> **v3 被推翻的两处**（都是自相矛盾，不是外部意见）：
> 1. §1.2 要求 artifact 内容与「当前会生成的」完全一致才算 `owned`，但 §3 又说
>    「解释器不存在 → `owned_stale`」——解释器一没，内容就对不上，只会被判成 `foreign`，
>    永远进不了 `owned_stale`。**归属与新鲜度必须分开判定。**
> 2. §4 要求把 `AI_USAGE_CONFIG` 快照进 artifact，但 XDG 的 `.desktop` 与 Windows 的
>    `.lnk` **都没有附加环境变量的槽位**（`Exec` 只有程序和参数；`.lnk` 只有
>    TargetPath/Arguments/WorkingDirectory）。四个 backend 里有两个做不到。
>
> 前置依赖已完成：公版默认值改造（`2026-08-02-public-defaults-design.md`，`da153ff`）。

## 目标

面板里加一个可开关的「登录时自动启动」选项，跨 Windows / macOS / Linux 通用。

硬约束，来自用户，优先级高于任何功能考量：

- **不开启就不碰用户系统**。默认关闭；只有显式点开关才写文件。
- **随时可取消**，恢复到操作前的状态。
- **绝不误伤不属于本功能的东西**。

第二条有个直接推论，v3 违反了、v4 必须守住：**任何情况下都不能出现「界面上关不掉」**。
平台探测失败、存在冲突项、解释器消失——这些可以阻止**开启**，但绝不能阻止**关闭**。

## 范围：分两阶段，本文只定阶段一

| 阶段 | 内容 | 状态 |
|---|---|---|
| **一** | 登录时自动启动 **daemon** | 本文定义 |
| 二 | 登录后自动**打开面板窗口** | 只列待解问题，见 §12 |

拆分理由：阶段一由 OS 的启动机制直接拉起 daemon，不需要中间 launcher，v1 里
「launcher 生命周期未定义」「端口探测误判」「探测与启动竞态」三类问题整体消失。

对本仓库作者（WSL）两阶段都不改变现状：daemon 已由 `deploy/install.sh` 的 systemd unit
管着，窗口已由 `~/.vscode-server/server-env-setup` → `ai-usage-panel` → `launcher.vbs` 拉起。
本功能面向外部用户。

## 非目标

- 跨平台托盘图标（`deploy/tray-widget.ps1` 保留为 Windows 侧可选增强）
- 把开关状态写进 `config.toml`（见〈状态即事实〉）
- 启动延迟、条件启动
- linger。**整体删除**，理由见 §2

---

## 〇、前置改造：显式配置入口

这一节是 v4 新增的，因为 v3 的 `AI_USAGE_CONFIG` 快照方案在 XDG 和 Windows 上无处落脚。

**根因**：`server/config.py` 的 `load_config()` 从环境变量 `AI_USAGE_CONFIG` 取配置路径，
而四个 backend 里只有 systemd 和 launchd 有环境变量槽位。

**解法**：改用命令行参数传配置——所有平台的启动机制都能传参数。

新增 `server/launch.py`：

```
<python> -m server.launch [--config <abs path>]
```

要求：

1. **strict 语义**。给了 `--config` 就必须能用：路径不存在、不是普通可读文件、解析失败
   → 打印原因并**非零退出**。**不得回落默认配置**。
   （现有 `load_config()` 在显式路径消失时会静默返回默认值，那对交互式使用无所谓，
   对一个每次登录自动跑的进程是静默的行为漂移。）
2. 路径在 `enable()` 时就绝对化，artifact 里只存绝对路径。
3. 不给 `--config` 时行为与今天的 `python -m server.main` 一致。
4. 配置必须在建 app **之前**确定。

配套小改动：`server/main.py` 现在有模块级 `app = create_app()`，导入即建应用。
已确认全仓库**没有任何地方**以 `server.main:app` 形式引用它（只有 `create_app()` 与
`python -m server.main`），因此把它移进 `main()` 是安全的，也让 `server.launch` 能干净地
先定配置再建 app。

**秘密不进 artifact**：只存配置文件路径，绝不把 `KIMI_API_KEY` 之类的值写进 artifact
（`.desktop`/`.lnk` 往往是 world-readable）。若用户的配置依赖当前 shell 里的秘密环境变量，
enable 时给出提示，建议改用配置文件里的 `api_key_file`。

---

## 一、归属与冲突

`deploy/install.sh` 装的是 `~/.config/systemd/user/ai-usage.service`。本功能必须能把它和
自己的东西分清，且在任何不确定的情况下**宁可什么都不做**。

### 1.1 独立命名

| 平台 | 本功能的 artifact |
|---|---|
| Linux systemd | `<systemd user dir>/ai-usage-autostart.service` |
| Linux XDG | `$XDG_CONFIG_HOME/autostart/ai-usage-autostart.desktop` |
| macOS | `~/Library/LaunchAgents/io.github.ai-usage.autostart.plist` |
| Windows | `<FOLDERID_Startup>\ai-usage-autostart.lnk` |

`$XDG_CONFIG_HOME` 未设置时才回落 `~/.config`；systemd 用户目录同理
（`install.sh` 用的就是 `$XDG_CONFIG_HOME`，判定和提示都必须跟它一致）。

### 1.2 marker：归属看快照，新鲜度看当下

**v3 的错误是拿同一组比较既判归属又判新鲜度。** v4 分开：

marker 里记录**生成当时的完整快照**：

```
version, project_root, python, config_path, generated_at
```

- **ownership（这是不是我写的？）**：marker 存在、版本可识别、`project_root` 规范化后等于
  当前进程的项目根、且 artifact 里的实际目标/参数/工作目录**与 marker 自述的快照一致**
  （即：没被人改过）。
  —— 注意比较对象是 **marker 的快照**，不是「现在会生成什么」。解释器换了、venv 重建了，
  它**依然是我写的**。
- **freshness（它还能用吗？）**：把 marker 快照与当前期望值比较，再跑 §3 的 probe。

三条件缺一即 `foreign`：不同项目根的同名 artifact 是 `foreign` 而非 owned——同一台机器上
两份 checkout 是常见情况。

**铁律：非 owned 的文件一律不覆盖、不删除。**

### 1.3 统一盘点，不只查当前 backend

每次 `status()` / `enable()` / `disable()` 都**同时盘点所有已知 artifact**，而不是只查当前
平台选中的那一个。用户可能从有 systemd 的机器切到没有的（或反之），backend 变化后会
同时留下两种启动项，两个都想绑同一个端口。

Linux/WSL 的盘点清单：旧 `ai-usage.service`、新 `ai-usage-autostart.service`、
XDG `ai-usage-autostart.desktop`。

### 1.4 既有部署：能确认多少说多少

v3 想凭 `WorkingDirectory` + `ExecStart` 断定「这是 install.sh 装的」。**证据不足**：
`ExecStart` 指向的是通用的 `uv`，任何用相同工作目录和命令的 unit 都会被误认；drop-in
还能在不改这两个字段的前提下加 `ExecStartPre`、条件、依赖。

v4 只做**能证明的断言**，分两级：

```
systemctl --user show ai-usage.service \
  --property=FragmentPath,DropInPaths,ExecStart,WorkingDirectory,UnitFileState
```

**`legacy_confirmed`**（可给出精确迁移步骤）需全部满足：

- `FragmentPath` 等于本机预期的用户 unit 路径（按 `$XDG_CONFIG_HOME` 计算）
- 该路径是当前用户拥有的**普通文件**，不是 symlink
- `DropInPaths` 为空
- `WorkingDirectory` 规范化后等于当前项目根，且 `ExecStart` 解析后确实跑本项目的服务

**`other`**（任何一条不满足）：只报告「检测到一个可能会占用同一端口的 unit」，
给出**实际查到的 `FragmentPath`** 和检查命令，**不给固定的 `rm` 命令**——
路径可能不是预期的那个，有 drop-in 时删主文件也解决不了问题。

两种情况都**不写任何文件**、都拒绝 enable。

迁移提示（仅 `legacy_confirmed` 时给），路径用**实际查到的 `FragmentPath`**：

```
systemctl --user disable --now ai-usage.service
rm <实际 FragmentPath>
systemctl --user daemon-reload
```

### 1.5 `installation_state`：只把能确定的当确定

`UnitFileState` 的取值远不止 enabled/disabled，且语义有陷阱：`static` 只表示没有
`[Install]` 规则，**不代表不会被依赖拉起**；`enabled-runtime` 只持续到重启。映射规则：

| `UnitFileState` | 判定 |
|---|---|
| `enabled` | 持久登记，下次登录会尝试启动 |
| `enabled-runtime` | **临时**登记，重启后失效，单列 |
| `disabled` | 不会自启 |
| `masked` / `masked-runtime` | 不会自启，且需先 unmask |
| `static` / `alias` / `indirect` / `generated` / `transient` / `linked` / `linked-runtime` | **unknown**（没做依赖分析就不能推 false） |
| 其他/查询失败 | unknown |

### 1.6 反向保护

`install.sh` 里加检测：发现 `ai-usage-autostart.service` **或** XDG 的
`ai-usage-autostart.desktop` 就提示并退出。这段检测必须

- 放在**任何写操作之前**（现有脚本会先 `uv sync` 建 `.venv`）
- **并且持有 §6.1 的同一把锁**直到整个安装结束。只做前置检查不拿锁，检查通过之后
  仍可能与面板的 enable 并发，照样产生两个 unit。

同时给 `deploy/ai-usage.service` 模板加一行部署 marker 注释——对存量部署无效，
所以 §1.4 仍以 `systemctl show` 为准，marker 只是给以后的部署多一层确认。

---

## 二、为什么不碰 linger

1. **语义相反**。用户级 service 本来就会在首次登录时由 user manager 启动；linger 的作用是
   「开机即启动，且最后一次注销后继续运行」——恰恰是用户明确不要的常驻。
2. **无法保证恢复**。linger 是独立的系统状态。用户原本就开着 linger 时，关闭本功能不能把它
   关掉（会影响别的服务），不关又留残迹。
3. **对阶段二有害**。linger 让服务在图形登录之前启动，拿不到 `DISPLAY`/Wayland 会话。

`deploy/install.sh` 现有的 linger 提示保持原样——那是「服务器式常驻」场景。

---

## 三、状态模型

`status()` **不读任何自己写的布尔配置**，直接查操作系统里的实际情况。

「文件存在」不等于「会运行」：macOS 13+ 可在「登录项」里关掉而保留 plist；Windows 可在
设置/任务管理器里禁用而保留 `.lnk`；XDG 的 `Hidden=true`、无效 `TryExec`、
`OnlyShowIn/NotShowIn` 都会让文件存在却不运行。

v2 用八值枚举，既不完备也不互斥。v3 拆成正交字段，但仍是**单值**，而 §1.3 明确允许多个
artifact 并存。v4 改成**每个 artifact 一条记录**：

```python
@dataclass
class ArtifactStatus:
    kind: str                        # systemd | xdg | launchd | windows | legacy_unit
    path: str
    ownership: str                   # absent | owned | foreign
    freshness: str                   # current | stale | unknown   （仅 owned 有意义）
    configured_for_next_login: bool | None   # None = 查不出
    active_now: bool | None
    detail: str | None

@dataclass
class AutostartStatus:
    platform: str
    supported: bool                  # 本平台是否有实现
    experimental: bool               # 是否未经实机验证（Windows）
    artifacts: list[ArtifactStatus]
    enabled: bool | None             # 见下方三值 OR
    can_enable: bool                 # 见下
    can_disable: bool                # 见下
    managed_by: str                  # panel | legacy_confirmed | other | none | unknown
    query_errors: list[str]
    issues: list[str]
    detail: str | None               # 给界面直接显示的人话
```

### `enabled` 的三值 OR

回答用户真正关心的问题——「下次登录它会不会自己起来」，因此 legacy 部署也算数：

```
任一 artifact 的 configured_for_next_login 为 True      → True
否则，存在任一为 None（含 query_errors 挡住判定）        → None
否则                                                    → False
```

v3 写成「查询失败即 None」是错的：一个机制已确认会启动、另一个查询失败时，答案确定是
**会启动**。

### `can_enable` / `can_disable` 必须分开

这是硬约束「随时可取消」的直接要求。v3 让 `conflicts` 和 `supported=False` 一并禁用开关，
后果是：**同时存在 owned 和 foreign artifact 时，用户反而删不掉自己那份**；
Windows 上 `pythonw.exe` 事后消失会让 `supported=False`，遗留的 `.lnk` 就再也清理不掉了。

- `can_enable`：需要平台受支持、无 foreign/legacy 冲突、目标解释器可用
- `can_disable`：**只要存在任何一条 `ownership == owned` 的 artifact 就为真**，
  与 `supported`、冲突、解释器是否还在**全部无关**。清理自己写的东西永远被允许。

### freshness 与 probe

`freshness` 只对 owned artifact 有意义，判定顺序：

1. marker 快照里的 `project_root` 目录存在、`server/launch.py` 存在
2. marker 快照里的解释器可执行
3. `config_path` 有值时该文件仍存在可读（§〇 的 strict 语义要求它必须在）
4. 跑一次 **probe** 确认依赖可导入、配置可解析

**probe 必须是专用的、无副作用的探针**，不能像 v3 写的那样 `import server.main`——
那会执行模块级 `create_app()`，把整套 FastAPI 与三个 provider 都导进来（实测约 0.32s、
峰值约 49MB），还会写 `__pycache__`，让一个只读的 `status()` 产生写入。要求：

- 专用 probe 代码，只做「导入必要模块 + 加载指定配置」，不建 app、不连网
- 以 `-I -B` 运行（隔离环境、不写 pyc），净化环境变量，关 stdin，丢弃输出
- 2–5 秒超时，用异步 subprocess 执行，**不得阻塞事件循环**
- **超时 ≠ 失败**：超时或无法执行 → `freshness = "unknown"`；
  探针明确报错（导入失败、配置解析失败）→ `freshness = "stale"`

`status()` **任何情况下不抛异常**，查询失败进 `query_errors`，其余维度照常返回。

### 状态 × 动作转移表

「覆盖自己的 artifact 是安全的」（marker 已证明归属），no-op 只适用于「已经正确开着」。

| ownership | configured_for_next_login | `enable()` | `disable()` |
|---|---|---|---|
| `absent` | — | 写入 + 注册 | **不是无条件 no-op**：先复查注册状态，有残留注册就清掉（见下） |
| `owned` | `True` 且 `freshness=current` | no-op | 注销 + 删除 |
| `owned` | `False` | 重新注册 | 删除 + 清注册 |
| `owned` | `None` | 重写 + 重新注册，之后**如实复查**并写 `issues` | 注销 + 删除 |
| `owned` | 任意，`freshness` 为 `stale`/`unknown` | **重写 + 重新注册**（修复路径） | 注销 + 删除 |
| `foreign` | 任意 | 拒绝 | 拒绝，不动它 |
| `legacy_confirmed`/`other` | 任意 | 拒绝，给 §1.4 的说明 | 不适用（不是本功能的东西） |

`absent` 时 `disable()` 不能无脑 no-op：macOS 上完全可能出现「plist 已删、job 仍在
launchd 里注册」，systemd 上也可能「文件已删、enable symlink 还在」。`disable()` 必须
把注册关系也查一遍并清理干净，才算真的恢复了。

「被系统禁用」（macOS 登录项、Windows 任务管理器）是**系统级用户意图**，重写 artifact
不一定能解除，所以上表要求「重写后如实复查」，不能假装修好了。

---

## 四、生成的启动项

`enable()` 把解释器、规范化的项目根、绝对化的配置路径**快照**进 artifact 的 marker，
不依赖 `uv`、不依赖 PATH。启动命令一律是：

```
<python> -m server.launch [--config <abs>]
```

四个平台统一走参数而非环境变量（§〇）。`python -m` 依赖 cwd 在 `sys.path` 里，
所以**每个 backend 都必须显式设置工作目录**（`.desktop` 用 `Path=`）。

**一律用平台原生序列化，禁止字符串拼 shell 命令**：plist 用 `plistlib`；`.desktop` 与
systemd unit 按各自转义规则处理（含空格、`%` 的路径必须正确转义）。

### Linux systemd

```ini
# ai-usage-autostart: 1
# project-root: /home/x/ai-usage
# python: /home/x/ai-usage/.venv/bin/python
# config: /home/x/ai-usage/config.toml     ← 无 --config 时省略此行
[Unit]
Description=ai-usage 面板（登录自启，由面板开关管理）
StartLimitIntervalSec=120
StartLimitBurst=3

[Service]
Type=simple
WorkingDirectory=<project-root>
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=<python> -m server.launch --config <abs>
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

⚠️ `StartLimitIntervalSec` / `StartLimitBurst` 属于 `[Unit]`（systemd v229 起）。
写在 `[Service]` 里会被当未知键忽略，限流就失效了。测试须对生成的文件跑
`systemd-analyze verify`。

写入后 `daemon-reload` + `systemctl --user enable`，**不加 `--now`**（§5）。

### macOS

`plistlib` 写 `Label` / `ProgramArguments=[python, "-m", "server.launch", "--config", ...]` /
`WorkingDirectory` / `RunAtLoad=true` / `AIUsageAutostart` marker。
plist 权限 `0o600`，**不得 group/world writable**（launchd 会拒绝加载）。

**enable 不做 `launchctl bootstrap`**——`bootstrap` 会把 job 载入当前 GUI domain，
配合 `RunAtLoad=true` 会立刻拉起第二个 daemon，撞上正在处理这个 HTTP 请求的实例。
`~/Library/LaunchAgents` 本来就在登录时被 per-user launchd 读取，写文件已经足够。

**disable 只删 plist，不做 `bootout`**。若当前 daemon 正是 launchd 在本次登录时启动的，
`bootout` 会在响应返回之前把自己杀掉。删掉 plist 后当前实例继续运行到注销，下次登录不再
启动——与 enable 的「只影响下次登录」对称。界面文案说明这一点。

**状态查询的限度**：`launchctl print gui/$UID/<label>` 只能说明**当前会话是否已加载**
（→ `active_now`），**不能**证明下次登录的授权状态。macOS 13+ 用户可在「登录项」里关掉。
因此：

- `active_now` ← `launchctl print`
- `configured_for_next_login` ← macOS 13+ 优先用 `SMAppService` 的 legacy plist 授权查询；
  **调不到该 API 时一律 `None`**，不得拿 `launchctl print` 顶替，也不得因为 plist 存在
  就报 `True`

### Linux XDG（无 user systemd 时的降级路径）

**必须如实说明它不是服务管理器**：XDG autostart 只在图形登录时把命令跑一次，
**不监督、不自动重启、daemon 崩了不会拉起来**。因此：

- 标 `experimental`，`issues` 里带说明
- 纯文字终端登录（无图形会话）时根本不会跑
- 界面文案区分：systemd 写「登录时自动启动并在崩溃后重启」，XDG 只写「图形登录时启动」

`.desktop` 写 `Type=Application` / `Exec`（正确引用）/ **`Path=<project-root>`** /
`X-AIUsage-*` marker。

状态判定读回文件：`Hidden=true` → 不会运行；`TryExec` 指向不存在的可执行 → 不会运行；
`OnlyShowIn`/`NotShowIn` **必须结合 `$XDG_CURRENT_DESKTOP` 实际计算**（见到字段就判
disabled 是错的）。

### WSL

走 systemd 路径，但界面文案改成「**随 WSL 启动**」而不是「登录时启动」：WSL 实例本身不随
Windows 开机，而是被 VSCode Remote-WSL 或 wsl 终端按需拉起。

检测：`/proc/version` 含 `microsoft`，或存在 `WSL_DISTRO_NAME`。
user systemd 不可用（未设 `systemd=true`）→ `supported=False` + 明确的开启方法
（但 `can_disable` 不受影响，见 §3）。

---

## 五、enable 只影响下次登录

`enable()` 不 `--now`、不 `bootstrap`、不 `kickstart`、不开第二个窗口。
`disable()` 同样只改下次登录，不去杀当前进程。

理由：用户是在**面板里**点这个开关的，daemon 此刻正跑着、正占着端口。立刻再拉一个只会撞
端口；反过来立刻杀掉，会杀死正在返回这个响应的进程。界面文案直说「下次登录生效」。

§3 的转移表保证这条规则不会掩盖「需要修复」的情形——no-op 只发生在
`owned + configured + current` 这一种组合上。

---

## 六、并发、失败与恢复

跨文件系统 + 服务管理器 + GUI 注册的多步操作**无法**给出真正的事务保证。
v4 的承诺是「尽力恢复 + 如实上报」，并把恢复顺序写死。

### 6.1 锁

**身份**：每用户一把，**跨 checkout、跨 backend 共用同一个身份**（否则两份 checkout 各锁
各的，照样能同时写出两个 unit）。锁文件放运行时目录（Unix 优先
`$XDG_RUNTIME_DIR`，回落到用户私有的固定路径），Windows 用具名互斥体。

**规则**：

- 只有 mutation（`enable`/`disable`）取锁；`status()`/`GET` **不取锁**，
  也**不得为了取锁而创建持久文件**（那会违反「关闭状态下零写入」）
- 拿不到锁 → API 返回 409，CLI 打印占用提示
- **锁文件释放后不 unlink**（否则新旧 inode 会让两批进程各自持锁，锁形同虚设）。
  它是运行时目录里的零字节文件，不算「残迹」；README 里说明它的位置和用途
- `install.sh` 必须在**前置检查之前**取得这把锁，并持有到安装结束（§1.6）

### 6.2 前向步骤与逆序补偿

先快照：**文件内容与元数据 + 注册状态 + 本次新建的目录**。然后按逆序补偿。

**enable（systemd）**

| 步骤 | 前向 | 补偿（逆序） |
|---|---|---|
| ① | 快照原文件与原注册状态 | — |
| ② | 原子写入 | — |
| ③ | `daemon-reload` | — |
| ④ | `systemctl --user enable` | — |

补偿顺序（v3 写错了，把恢复文件排在 reload 之后，导致内存里是新 unit、磁盘上是旧 unit）：

1. **仅撤销本事务新建的注册关系**——操作前就已经 enabled 的，不能顺手 disable 掉
2. 恢复旧文件 / 删除本次新建的文件
3. `daemon-reload`（让 systemd 内存与磁盘一致，必须排在恢复文件**之后**）
4. 恢复原注册状态并复查

**disable 需要自己的一张表，不能复用 enable 的**。典型失败：「文件删成功、
`daemon-reload` 失败」——下次 `disable()` 看到 `absent` 若直接 no-op 就永远收不了尾。
这正是 §3 转移表要求 `absent` 时仍复查注册关系的原因。

其余 backend（launchd / XDG / Windows）只有「备份 → 原子写入」两步，补偿即「恢复备份 /
删除新文件」；Windows 多一步「写临时 `.lnk` → 读回校验 → `os.replace`」。

### 6.3 通用要求

- 原子替换：临时文件写在**同一目录**再 `os.replace()`。目标是 symlink → **直接拒绝**，不跟随
- **已有 owned artifact 更新失败时恢复原文件，不能直接删除**（那会把本来好好的自启动弄没）
- 只删本次**新建**的目录
- 所有 `systemctl` / `launchctl` / `powershell` 调用带 10s 超时，超时按失败处理

### 6.4 补偿失败时如实上报

返回 `recovery_required`，`detail` 里给出精确到路径与命令行的手工修复步骤。
这比一个兑现不了的绝对承诺诚实。

### 6.5 验收断言

**`disable` 后的断言是「恢复到操作前的目录快照」，不是「目录变空」。**
`~/.config/systemd/user`、`~/Library/LaunchAgents`、`autostart` 都是共享目录。

---

## 七、Windows：做，但标 experimental

README 目前要求 Windows 用户把 daemon 跑在 WSL 里，项目从未在原生 Windows 上验证过。
按用户决定：**实现 Windows backend，但不做实机验证**，必须在文档与 UI 上如实标注
`experimental`，不得把未验证的路径包装成已支持。

代码层面已确认无 Unix 硬依赖：路径全走 `expanduser()`。已知风险一处：codex 若装成 `.cmd`
包装器，子进程可能起不来——但 Codex provider 有「读会话日志」的兜底降级。

实现要点：

- 启动目录用 `SHGetKnownFolderPath(FOLDERID_Startup)` 经 `ctypes` 解析，**不硬编码**
  `%APPDATA%\Microsoft\...`（可被重定向）。注意 COM 初始化与 `CoTaskMemFree`。
- **不用 VBScript**（微软已启动退役流程）。用 `.lnk` 指向 `pythonw.exe`（无控制台窗口），
  参数 `-m server.launch [--config ...]`，工作目录为项目根。
- `pythonw.exe` 解析：取 `sys.executable` 同目录下的 `pythonw.exe`；**不存在时不要猜**，
  `can_enable=False` + 原因。注意这**不影响 `can_disable`**（§3）。
- `.lnk` 经 PowerShell 的 `WScript.Shell` COM 创建。**禁止把路径插值进 `-Command` 字符串**
  ——用参数数组或固定脚本 + 安全传参，否则含引号/`$` 的路径就是命令注入。
- 先写临时 `.lnk` → **读回校验** Description marker / TargetPath / Arguments /
  WorkingDirectory → 再 `os.replace` 到启动目录。
- 「文件存在」不等于会运行。查不到授权状态 → `configured_for_next_login = None`，
  文案区分「已登记」与「系统确认会运行」。

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

之前各版的三重校验只防「恶意网页自己发跨域请求」。攻击者可以把**本地面板本身**放进透明
iframe 诱导用户点真开关——请求由面板自己发出，Host、Origin、自定义头全部合法。

**全局响应中间件**给所有响应加：

```
Content-Security-Policy: frame-ancestors 'none'
X-Frame-Options: DENY
```

必须是 HTTP 响应头，`<meta>` 里的 CSP 不支持 `frame-ancestors`。再加一道：
**首次开启需要界面上的二次确认**，让一次误点不足以写入启动项。

### 8.2 `require_local_ui` 依赖

套在 `PUT /api/autostart` 和现有的 `POST /api/refresh` 上：

1. **Host 头**：精确匹配 `127.0.0.1:<port>`、`localhost:<port>` 或 `[::1]:<port>`。
   缺失、重复、畸形一律拒绝。**不信任 `X-Forwarded-Host`。**
2. **自定义头**：要求 `X-Requested-By: ai-usage-panel`。跨域携带非 safelisted 自定义头必然
   触发预检，而服务端不注册任何 CORS 中间件，预检必然失败。
3. **Origin 头**：**fail-closed**。必须存在，且精确等于由 Host 推导出的
   `http://<host>:<port>`；缺失、`null`、畸形、不匹配一律 403。
   （早期版本曾以「Safari 同源 POST 不发 Origin」为由放行缺失值。**该理由不成立**：
   Fetch 规范要求非 GET/HEAD 请求携带 Origin，WebKit 2008 年 bug 20792 已修。
   CLI 兜底走 `python -m server.autostart`，根本不经过 HTTP。）

`POST /api/refresh` 的加固可独立成一个提交。

---

## 九、界面

面板底部一个克制的设置区：默认收起，点齿轮展开。README 强调面板「只有一处是响的」
（节奏刻度），设置区不该抢戏。

- 开关的可用性**分别**由 `can_enable` / `can_disable` 决定，不共用一个 disabled 判断
- `experimental=True`：开关旁一枚 experimental 标记
- `managed_by` 为 `legacy_confirmed`：禁止开启 + §1.4 的完整迁移三步（路径用实际查到的）
- `managed_by` 为 `other`：禁止开启 + 检查命令，**不给删除命令**
- `enabled=None`：不确定态，配一行「系统未告知是否会运行」，**不要**画成关闭
- 存在 `freshness != current` 的 owned artifact：提示「点一下开关即可修复」
  （转移表保证这条真的有效）
- 措辞不夸大：systemd `enabled` 只证明**已登记**。文案用「已登记，下次登录将尝试启动」，
  不用「系统确认会运行」
- 操作失败：就地显示原因，不弹窗；`recovery_required` 时显示手工修复命令
- 首次开启：一步确认

---

## 十、测试

新增 `tests/test_autostart.py`，安全用例并入 `tests/test_api.py`。用 `monkeypatch` 假冒 HOME
与平台检测。

**不碰系统**
- 关闭状态下反复 `status()`，断言目标目录零写入（比对前后快照），**且不产生 `__pycache__`**
- `status()` 不创建锁文件
- 目录中预置无关文件，走完 enable → disable，断言原样保留

**归属与冲突**
- marker 记录的解释器**已不存在** → 仍判 `owned`，`freshness` 非 current
  （这是 v3 的自相矛盾，必须有回归测试）
- 同名但 `project_root` 指向另一个 checkout → `foreign`，拒绝操作
- artifact 内容被人手改过（与 marker 快照不符）→ `foreign`
- `legacy_confirmed` 四条件逐一破坏（FragmentPath 不符 / symlink / 有 DropInPaths /
  WorkingDirectory 不符）→ 降级为 `other`，且提示里**不含 `rm`**
- 迁移提示里的路径来自实际 `FragmentPath`，不是硬编码的 `~/.config/...`
- systemd 与 XDG artifact **同时存在** → `artifacts` 有两条
- owned + foreign 并存 → `can_enable=False` 但 **`can_disable=True`**，且 disable 只删 owned
- `supported=False`（如 `pythonw.exe` 消失）时 **`can_disable` 仍为 True**
- 目标路径是 symlink → 拒绝，不跟随
- `installation_state` 全部取值的映射，含 `enabled-runtime`、`static`、`generated`
- `install.sh` 在任何写操作之前拦下两类 artifact，**且持锁**

**状态与转移**
- 各 backend 的 `absent → owned → absent` 全循环
- `enabled` 三值 OR：一个 True + 一个查询失败 → **True**（不是 None）
- `absent` + 残留注册关系 → `disable()` 清理注册，不是 no-op
- `stale` / `configured=None` → `enable()` 确实执行重写（**不是 no-op**）
- XDG `Hidden=true`、无效 `TryExec` → 不会运行；`OnlyShowIn`/`NotShowIn` 结合
  `$XDG_CURRENT_DESKTOP` 计算，两种结果都要测
- macOS：plist 在、`launchctl print` 查得到 → `active_now=True` 但
  `configured_for_next_login` 仍为 `None`（无法调授权 API 时）

**probe**
- probe 以 `-I -B` 运行，不产生 pyc
- probe 超时 → `freshness="unknown"`（**不是 stale**），且子进程被清理
- probe 明确报错（导入失败 / 配置解析失败）→ `freshness="stale"`
- probe 不阻塞事件循环（并发 `status()` 不串行化）

**失败与补偿**
- 逐步故障注入：写入失败 / `daemon-reload` 失败 / `enable` 失败，各自补偿到操作前快照
- **补偿顺序断言**：恢复文件在 `daemon-reload` **之前**
- 操作前 unit 已 enabled → 补偿**不得**把它 disable 掉
- 已有 owned artifact 更新失败 → **恢复原文件**，不是删除
- 补偿本身失败 → `recovery_required` + `detail` 含具体修复命令
- `disable` 半成功后再调一次能收尾
- 跨进程锁：CLI 与 API 并发 → 一方 409/占用提示；锁文件释放后**仍存在**

**入口与配置**
- `python -m server.launch --config <不存在>` → 非零退出，**不回落默认配置**
- `--config` 指向不可读文件 / 解析失败 → 非零退出
- 不给 `--config` 时行为与 `python -m server.main` 一致
- `config_path` 被绝对化后写入 artifact；秘密值不出现在 artifact 里
- 生成的 unit 过 `systemd-analyze verify`
- 含空格、`%`、引号的路径在四种格式里都正确转义

**API 安全**
- Host：合法三种（含 `[::1]:port`）通过；缺失/重复/畸形/外部域名拒绝；
  `X-Forwarded-Host` 不影响判定
- 缺 `X-Requested-By` → 403
- Origin：匹配通过；缺失 / `null` / 畸形 / 不匹配一律 403
- **真实预检报文**（不是裸 `OPTIONS`）：
  ```
  OPTIONS /api/autostart
  Origin: https://evil.example
  Access-Control-Request-Method: PUT
  Access-Control-Request-Headers: content-type,x-requested-by
  ```
  断言：无匹配的 `Access-Control-Allow-Origin`；未允许 PUT 与上述 header；
  **backend mutation 调用次数为 0**
- 所有响应含 `frame-ancestors 'none'` 与 `X-Frame-Options: DENY`
- 并发 `PUT` → 一个成功一个 409
- `/api/refresh` 加固后原有行为不回归

---

## 十一、卸载

- README 增加一节：四平台的 artifact 路径、marker 长什么样、锁文件位置、如何手工删除
- CLI 兜底：`python -m server.autostart status|disable`——面板打不开时仍能关掉自启动。
  不经过 HTTP，不受 §8 校验影响，但**同样要拿 §6.1 的锁**。

---

## 十二、阶段二的待解问题（不在本文定稿）

1. **GUI 会话可用性**。启动时可能还没有 `DISPLAY`/Wayland/登录会话。Linux 上窗口层应走
   XDG autostart 而非 systemd；macOS/Windows 用各自的 GUI 登录机制。
2. **launcher 生命周期契约**：`exec` 掉自己、常驻监督、还是拉起子进程后退出
   ——对三个平台语义完全不同。
3. **别把别的服务当成 ai-usage**：只看端口开着会误判，必须打 `/api/health`。
4. **浏览器枚举**大幅简化。
5. **重复窗口**：用户已经开着面板时不该再弹一个。

---

## 评审项处置对照

### 第三轮（对 v3）：4 阻断 + 2 重要 + 1 次要

| 意见 | v4 处置 |
|---|---|
| 阻断 1 legacy 认定证据不足 | §1.4 拆 `legacy_confirmed` / `other` 两级；加 FragmentPath 归属、非 symlink、DropInPaths 为空；迁移命令用实查路径；`other` 不给 `rm` |
| 阻断 2 状态模型不完备 | §3：改 `artifacts: list`；ownership 比 marker 快照、freshness 比当下（修掉 v3 的自相矛盾）；`enabled` 三值 OR；拆 `can_enable`/`can_disable`；`absent` 时 disable 仍清注册 |
| 阻断 3 补偿顺序错 + 锁不覆盖竞态 | §6.1 锁身份跨 checkout、释放不 unlink、status 不取锁、install.sh 持锁；§6.2 补偿顺序改为「撤销注册 → 恢复文件 → reload → 复查」，disable 单列一张表 |
| 阻断 4 配置快照无处可放 | §〇 新增 `server/launch.py`，四平台统一用 `--config` 参数；strict 语义；`main.py` 的模块级 `app` 移进 `main()`（已核实无引用）；秘密不入 artifact |
| 重要 1 probe 需受控 | §3 probe 段：专用探针、`-I -B`、净化环境、超时、异步、超时→unknown 而非 stale |
| 重要 2 OPTIONS 断言不够具体 | §10：改成完整预检报文 + 三条断言 |
| 次要 `UnitFileState` 写得过满 | §1.5 全表，`static`/`alias`/`generated` 等归 unknown |

### 前两轮遗留（复审已判通过的不再重复）

linger 删除、点击劫持防护、两阶段拆分、Windows experimental 标注、
阶段一无 launcher、XDG 不监督的如实标注、`StartLimit` 移入 `[Unit]`、
Origin fail-closed、`owned_enabled` 措辞——均在 v3 通过，v4 保持。
