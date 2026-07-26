# ai-usage 设计文档

> 状态：已确认（2026-07-26）。本文档是实施的唯一事实源。

## 1. 背景与目标

此前只能靠浏览器常驻三个 AI 对话页（Claude / ChatGPT / Kimi），进各家 account 页看订阅额度，
三个重型 SPA 常驻内存 ~1GB。目标：做一个**美观、轻量、常驻可用**的本地额度面板，
一屏看清三家订阅的额度水位与重置时间。

**成功标准**：
- 一眼看清 Claude（5h + 周）、Codex（5h + 周）、Kimi（5h + 周/月）的用量百分比与重置倒计时；
- 总常驻资源 ≤ 100MB（daemon ~30MB + 一个浏览器 app 小窗）；
- 不烧任何对话额度（只调账户元数据接口）；
- 将来迁移 macOS 零改动（另可选 SwiftBar 读 JSON API 做菜单栏图标）。

## 2. 形态决策记录（为什么是 Web 面板）

调研结论（2026-07-26）：
- 社区单家监控工具成熟，但**无一同时覆盖 Claude+Codex+Kimi**，自研有正当性；
- 三家取数方案均有社区已验证实现可参考（见 §4 各 provider 的"参考实现"）；
- 已否决的备选：① fork bozdemir/claude-usage-widget 加 Kimi（UI 受限于上游，
  WSLg 下无边框/透明/置顶效果不可靠）；② 自研 PySide6 走 WSLg（同样吃 WSLg 折损，
  Qt 精修 UI 成本高于 CSS）。
- 选定：WSL 内小 daemon + localhost 页面，Windows 用 `msedge --app=` 开成无地址栏
  小窗（观感近原生 widget），macOS 将来直接复用。

## 3. 架构

```
ai-usage/
├── server/
│   ├── main.py            # FastAPI + uvicorn，绑定 127.0.0.1:8788（端口可配）
│   ├── poller.py          # 后台轮询：默认 300s/次；单 provider 失败指数退避（最长 30min），不影响其他
│   ├── cache.py           # 内存缓存 + 落盘 data/cache.json（重启秒开，显示"数据来自 X 分钟前"）
│   ├── config.py          # 读 config.toml
│   └── providers/
│       ├── base.py        # Provider 契约（见 §4.0）
│       ├── claude.py
│       ├── codex.py
│       └── kimi.py
├── web/                   # 纯静态：index.html + style.css + app.js，零构建链、零 node 依赖
├── deploy/
│   ├── ai-usage.service   # systemd user unit（WSL 开机自启）
│   ├── install.sh         # 安装/启用 unit
│   └── windows-shortcut.md# Windows 侧快捷方式做法（msedge --app）
├── docs/specs/            # 本文档
├── config.example.toml    # 模板；真实 config.toml 在 .gitignore 中
├── tests/                 # pytest：各 provider 响应解析的 fixture 单测
└── README.md
```

**技术栈**：Python ≥3.11，依赖只要 `fastapi`、`uvicorn`、`httpx`（+dev: pytest）。
用 `uv` 管理虚拟环境。前端原生 HTML/CSS/JS。

**HTTP API**：
- `GET /` → 面板页面
- `GET /api/summary` → `{"updated_at": ..., "providers": [ProviderUsage, ...]}`
- `POST /api/refresh?provider=<id|all>` → 立即刷新并返回新 summary

## 4. Provider 层

### 4.0 统一契约（base.py）

```python
@dataclass
class UsageWindow:
    id: str          # "5h" | "week" | "week_opus" | "month" ...
    label: str       # 显示名，如 "5小时窗口"
    used_pct: float  # 0-100
    resets_at: datetime | None

@dataclass
class ProviderUsage:
    id: str                  # "claude" | "codex" | "kimi"
    name: str                # 显示名
    plan: str | None         # 订阅档位显示名
    windows: list[UsageWindow]
    status: str              # "ok" | "stale" | "auth_expired" | "error"
    error: str | None
    fetched_at: datetime

class Provider(Protocol):
    id: str
    async def fetch(self) -> ProviderUsage: ...
```

每个 provider 完全独立：一家挂了（凭证过期/断网）只显示该卡片的错误态，其余照常。

### 4.1 Claude

- **凭证**：只读 `~/.claude/.credentials.json`。**实测确认（2026-07-26）**的字段路径：
  `claudeAiOauth.accessToken`（Bearer token）、`claudeAiOauth.expiresAt`（**毫秒**时间戳）、
  `claudeAiOauth.subscriptionType`（订阅档位字符串，用作 plan 显示）。
  同文件还有 `mcpOAuth.*` 等无关字段，忽略。
- **端点**：`GET https://api.anthropic.com/api/oauth/usage`。
  **实测确认**：只需 `Authorization: Bearer <accessToken>` 一个头即可 200，
  **不需要 `anthropic-beta` 头**（带与不带均 200，行为一致）。
- **返回（结构以实测为准；下列数值均为示意占位）**：顶层同时给"具名窗口"和统一的
  `limits[]` 数组，实现**以 `limits[]` 为准**（更规范、可扩展），具名字段作交叉校验：
  ```jsonc
  {
    "five_hour":  {"utilization": 0.0, "resets_at": "2026-01-01T00:00:00.000000+00:00", "limit_dollars": null, "used_dollars": null, "remaining_dollars": null},
    "seven_day":  {"utilization": 0.0, "resets_at": "2026-01-03T00:00:00.000000+00:00", ...},
    "seven_day_opus": null,        // 该档无此窗口时为 null；其余 seven_day_* / 代号字段同理
    "extra_usage": {"is_enabled": true, "monthly_limit": 10000, "used_credits": 0.0,
                    "utilization": 0.0, "currency": "USD", "decimal_places": 2, ...},
    "limits": [
      {"kind": "session",     "group": "session", "percent": 0, "severity": "normal", "resets_at": "...", "scope": null, "is_active": false},
      {"kind": "weekly_all",  "group": "weekly",  "percent": 0, "severity": "normal", "resets_at": "...", "scope": null, "is_active": true}
    ],
    "spend": {"used": {"amount_minor": 0, "currency": "USD", "exponent": 2},
              "limit": {"amount_minor": 10000, ...}, "percent": 0, "severity": "normal", "enabled": true, ...}
  }
  ```
  映射规则：`limits[].kind` → UsageWindow.id（`session`→`5h`、`weekly_all`→`week`、
  `weekly_opus`→`week_opus`，未知 kind 直接透传 kind 作 id 并用 kind 生成 label），
  `percent` → used_pct，`resets_at` → ISO8601 解析为 aware datetime。
  **额外收获**：`extra_usage` 是**独立计费的 credit 池**（不占订阅周额度），
  按月重置，v1 一并作为一个 window 显示（id `extra_credits`，
  used_pct 取 `extra_usage.utilization`，resets_at 未在报文中给出 → None），
  仅当 `extra_usage.is_enabled` 为真时显示。
- **网络**：必须走代理 `http://127.0.0.1:7890`（httpx 显式传 proxy，**不依赖 .bashrc**
  ——本机非交互进程不读 .bashrc，裸连必吃 403，这是踩过的坑）。
- **token 过期**（credentials 里有 expiresAt）：v1 不实现 refresh 流程，
  状态置 `auth_expired`，卡片提示"随便用一次 claude CLI 即自动续期"。
- **参考实现**：bozdemir/claude-usage-widget 源码。

### 4.2 Codex

- **首选**：临时拉起 `codex app-server --stdio`（JSON-RPC，一行一条 JSON），
  取到限额百分比与重置时间后即关进程，不常驻。
  **必须经 `codex-nowin` 包裹拉起**（interop 硬隔离铁律）；启动时显式传代理 env（同 §4.1 的坑）。
  **实测确认（2026-07-26）**的最小握手序列：
  ```
  --> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"...","version":"..."}}}
  <-- {"id":1,"result":{"userAgent":"...","codexHome":"...","platformFamily":"unix","platformOs":"linux"}}
  --> {"jsonrpc":"2.0","method":"initialized","params":{}}
  --> {"jsonrpc":"2.0","id":2,"method":"account/rateLimits/read","params":{}}
  ```
  服务端会主动推送无关通知（实测见过 `remoteControl/status/changed`），
  必须**按 id 匹配响应**、忽略通知。响应结构（数值为示意占位）：
  ```jsonc
  {"rateLimits": {"limitId":"codex","limitName":null,
     "primary":  {"usedPercent": 0, "windowDurationMins": 10080, "resetsAt": 1780000000},  // epoch 秒
     "secondary": null,                                    // 有值时结构同 primary
     "credits":  {"hasCredits": false, "unlimited": false, "balance": "0"},
     "individualLimit": null, "spendControlReached": false,
     "planType": "<枚举>", "rateLimitReachedType": null},
   "rateLimitsByLimitId": {"codex": {/* 同上 */}},
   "rateLimitResetCredits": {"availableCount": 0, "credits": []}}
  ```
  `windowDurationMins`：`300` = 5 小时窗口，`10080` = 7 天窗口。
  ⚠️ **禁止调用 `account/read`**——它返回账号邮箱，本项目不需要也不得取。
- **兜底**：解析 `~/.codex/sessions/**/rollout-*.jsonl` 中最近一条 rate_limits 快照
  （xiangz19/codex-ratelimit 的做法）——零成本但数据停留在最后一次使用时刻，
  卡片需标注 `stale` + 数据时刻。**实测确认**的记录形状（注意是 snake_case，
  与 app-server 的 camelCase 是两套命名）：
  ```jsonc
  {"timestamp":"...","type":"event_msg","payload":{"type":"token_count",
    "info":{/* token 统计，本项目不用 */},
    "rate_limits":{"limit_id":"codex",
      "primary":{"used_percent":0.0,"window_minutes":10080,"resets_at":1780000000},
      "secondary":null,"credits":{...},"plan_type":"<枚举>", ...}}}
  ```
  兜底数据的 `fetched_at` 用该记录自身的 `timestamp`，不是当前时间。
- app-server 路径失败超过 N 次自动降级到兜底路径，并每隔一段时间重试恢复。

### 4.3 Kimi

- **端点（实测确认，2026-07-26）**：`GET https://api.kimi.com/coding/v1/usages`，
  请求头仅需 `Authorization: Bearer <key>` + `Accept: application/json`。
- **鉴权决策**：用 **`sk-kimi-*` API key**（实测 200，响应 `authentication.method`
  为 `METHOD_API_KEY`）。
  **已否决 oauth 直调**：`~/.kimi-code/credentials/` 里的 access_token 虽然同样能 200，
  但**寿命仅 15 分钟**，续期要 POST `https://auth.kimi.com/api/oauth/token`
  （`grant_type=refresh_token`）且**服务端会轮换 refresh_token**——daemon 与 kimi CLI
  争抢同一份凭证文件，有把 CLI 登录挤掉的风险，不值得。
  key 来源按优先级：config 的 `api_key` → 环境变量（默认 `KIMI_API_KEY`）→
  密钥文件（默认 `~/.config/shell/secrets.sh`，正则提取，**禁止 source/exec**）。
- **网络**：国内直连，**不走代理**（httpx 该 provider 不传 proxy，且 `trust_env=False`
  防止环境变量里的代理意外生效）。
- **返回（结构以实测为准；数值为示意占位）**：
  ```jsonc
  {"user": {/* 账号身份信息，一律不取不存不记日志 */},
   "usage":  {"limit":"100","used":"0","remaining":"100","resetTime":"2026-01-03T00:00:00.000000Z"}, // 长周期（周）窗口
   "limits": [{"window":{"duration":300,"timeUnit":"TIME_UNIT_MINUTE"},
               "detail":{"limit":"100","used":"0","remaining":"100","resetTime":"..."}}],            // 300 分钟 = 5 小时窗口
   "parallel": {...}, "totalQuota": {},
   "authentication": {"method":"METHOD_API_KEY","scope":"FEATURE_CODING"},
   "subType": "<枚举>", "domain": "DOMAIN_NEXUS"}
  ```
  ⚠️ 三个坑：① `limit`/`used`/`remaining` 都是**字符串**；
  ② 报文**只给绝对值不给百分比**，`used_pct = used / limit * 100`（limit 为 0 时置 0）；
  ③ `resetTime` 带 `Z` 与 6 位小数，Python 3.11+ 的 `fromisoformat` 可直接解析。
  映射：`limits[]` → `5h`（按 `window.duration` 换算），顶层 `usage` → `week`；
  `plan` 取 `subType` 去掉 `TYPE_` 前缀。

## 5. 前端（web/）

- 单页竖排卡片（Claude / Codex / Kimi 各一张），适配 ~380×540 的 app 小窗；
- 每窗口一条进度条：<70% 正常色、≥70% 橙、≥90% 红；百分比用等宽数字；
- 重置时间显示相对倒计时（"周三 08:00 重置 · 剩 2 天 3 小时"）;
- 顶部/底部：最近刷新时间 + 手动刷新按钮（调 `/api/refresh`）；
- 深浅色跟随系统（`prefers-color-scheme`）；
- 错误态卡片：明确显示原因与修复提示（如凭证过期提示语）；stale 数据显示数据时刻；
- 页面无外部资源引用（字体用系统栈），保证离线可开、加载即秒开；
- 实施时使用 frontend-design / dataviz skill 精修视觉。

## 6. 部署与使用

- **WSL**：`deploy/ai-usage.service`（systemd user unit），`install.sh` 负责
  `systemctl --user enable --now`；日志走 journald。
- **Windows**：`deploy/windows-shortcut.md` 说明建快捷方式：
  `msedge --app=http://localhost:8788 --window-size=400,560`（WSL2 localhost 转发直达）。
- **macOS（将来）**：同一仓库直接跑；可选 SwiftBar 脚本读 `/api/summary` 做菜单栏图标
  （backlog，不在 v1）。

## 7. 安全约束

- 服务只绑 `127.0.0.1`；
- 三家凭证**只读复用**各 CLI 已有文件，token 只在内存，绝不写日志/落盘/入 git；
- `.gitignore` 首日就位：`config.toml`、`data/`、`.venv/`、`__pycache__/` 等；
- 日志里 URL/header 一律脱敏。
- **仓库内不得留个人数据**（含提交历史）：真实用量数值、账号档位/订阅等级、邮箱、
  userId、本机绝对路径等一律不写进代码、文档、fixture 与提交信息；
  示例一律用中性占位值。三家 provider 的账号身份字段（Claude 的 `organizationUuid`、
  Codex 的 `account.email`、Kimi 的 `user.*`）**不取、不存、不记日志、不上页面**。
- **凭证不得进入执行层（Codex/Kimi/subagent）的上下文**：涉及真实 token 的端点实测
  一律由主会话执行（用 shell 变量/`curl -K -` 传递，token 绝不出现在命令行与输出里），
  实测得到的**响应报文结构**（不含凭证）才交给执行层写解析代码。
  委派 prompt 必须显式禁止 `cat`/打印 `~/.claude/.credentials.json`、
  `~/.codex/auth.json`、`~/.kimi-code/{oauth,credentials}/*`。

## 8. 分阶段实施与验收

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| P1 | 骨架（server+config+cache+poller）+ Claude provider + 极简页面 | 浏览器开 localhost:8788 能看到 Claude 真实 5h/周百分比；pytest 绿 |
| P2 | Codex provider（app-server 实测 + sessions 兜底） | 面板出现 Codex 真实数据；拔掉网络能降级显示 stale 数据 |
| P3 | Kimi provider（oauth 直调实测，sk-key 兜底） | 面板出现 Kimi 真实数据 |
| P4 | UI 精修 + systemd + Windows 快捷方式文档 + README + push GitHub | 重启 WSL 后服务自启；msedge app 窗观感达标；仓库已推送 |

每阶段结束 git commit；委派执行层前先拍快照（全局安全规则）。

## 9. v1 明确不做（backlog）

历史曲线、阈值告警/通知、系统托盘图标、Tauri 壳、Claude token 自动 refresh、
SwiftBar 脚本、多用户/远程访问。

## 10. 执行路由建议（给指挥会话）

- P1/P4 spec 明确 → 优先派 **Kimi**；
- P2/P3 含"端点/报文实测"的诊断性工作 → 派 **Codex**（可先用 read-only 沙箱探明，再 workspace-write 实现）；
- 指挥会话（Opus）负责拆解、审核、集成测试；大 diff 按审核链先派 Codex 审。

**实际执行时的调整（2026-07-26 记录）**：三家的端点/报文实测**全部由指挥会话自己做**——
因为实测必然要碰真实 token，而凭证不得进执行层上下文（见 §7）。实测完成后，
P2/P3 就从"诊断型"降为"spec 明确型"：P2 因涉及 asyncio 子进程生命周期管理仍派 Codex，
P3 已无难点故改派 Kimi。
