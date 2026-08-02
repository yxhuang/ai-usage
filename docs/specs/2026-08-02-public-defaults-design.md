# 公版默认值改造

2026-08-02

## 背景

项目公开在 GitHub，README 宣称 `no config file needed` 开箱即用。但代码里的默认值
其实是作者的私人环境，外部用户 clone 下来三家都取不到数：

| 私人默认 | 外部用户会怎样 |
|---|---|
| `proxy = "http://127.0.0.1:7890"` | 作者本机 FlClash 端口。没有代理的用户 → Claude / Codex connection refused |
| `command = "codex-nowin"` | 作者私有的包裹脚本，别人机器上不存在 → Codex 拉不起 app-server |
| `api_key_file = "~/.config/shell/secrets.sh"` | 作者用 chezmoi 管的文件 → Kimi 取不到 key |

且**代理无法通过配置禁用**（已实测）：不写 `proxy` 时深度合并保留默认 7890；写
`proxy = ""` 时 httpx 抛 `ValueError: Unknown scheme for proxy URL`；TOML 没有 null。
外部用户唯一出路是改源码。

## 目标

单一 `main` 分支。代码默认值 = 通用值；作者的私人值放进不入库的 `config.toml`
（已建立并验证）。**不开自用分支** —— 差异全是配置、没有代码差异，分支只会制造
双线合并成本。

## 代理语义（本次核心）

定义两态，不引入 `proxy_enabled` 之类的第二个字段（会产生
`enabled=false + proxy=URL` 这类自相矛盾的组合）：

| 配置 | 语义 |
|---|---|
| 缺失 / `""` / 纯空白 | **直连**，且必须忽略环境变量里的代理 |
| 非空 URL | 使用该代理 |

⚠️ **光把空值归一化成 `None` 不够** —— 已确认两处执行逻辑会让环境代理漏进来：

- `providers/claude.py` 未设 `trust_env=False`，httpx 默认会读 `HTTP_PROXY` 等环境变量
- `providers/codex.py` 用 `os.environ.copy()` 复制完整父环境，`None` 时只是"不覆盖"

所以配置层归一化与 provider 执行逻辑**必须同时改**，否则"直连"名不副实。

## 改动清单

### 代码

私人默认值在五处重复，`Config` 应成为唯一默认值来源，`main.py` 不再维护第二套 fallback：

1. `server/config.py` — 三家内置默认改通用：claude/codex 的 `proxy` 去掉，codex 的
   `command` 改 `"codex"`，kimi 的 `api_key_file` 改 `None`。新增 `proxy` 归一化
   （`strip()`、空串转 `None`、非法类型报明确的配置错误）。
2. `server/main.py` — 删掉硬编码的 `"codex-nowin"`、`7890`、`secrets.sh` 三个 fallback，
   一律从 `Config` 取。
3. `server/providers/codex.py` — 构造器默认改 `command="codex"`、`proxy=None`；
   `proxy is None` 时**主动从子进程环境中删除** `http_proxy`/`https_proxy`/`all_proxy`
   及其大写形式，而不是只跳过覆盖。
4. `server/providers/claude.py` — 构造器 `proxy=None` 时传 `trust_env=False`；
   把"裸连必 403"的注释改为限定说法（那是作者所在网络环境的事实，不是普遍事实）。
5. `server/providers/kimi.py` — `api_key_file` 默认 `None`；为 `None` 时跳过文件来源
   （不得 `Path(None)` 崩溃）；错误文案里的 `secrets.sh` 改成"配置的密钥文件"。
   同时补上与另两家一致的可选 `proxy` 参数（默认直连）——企业网络用户需要它。

### 文档

6. `config.example.toml` — 改成通用模板：代理与密钥文件改为**注释掉的示例**，
   剥离"本机默认""禁止改成裸 codex"等个人化描述。
7. `README.md` / `docs/README.zh-CN.md` — 同步默认值与代理语义；把"装并登录任一 CLI
   即可复用凭证"改成**逐 provider 的真实前置条件表**（Kimi 并不读 CLI 登录态，
   只认 API key）；修正"daemon 不继承代理环境变量"这一与实现相反的表述；
   把 `never sends your credentials anywhere` 改成"除对应厂商外不发往任何地方"。
8. `CHANGELOG.md` — 记录默认行为变更（属 breaking change 级别的提示）。
9. `docs/specs/2026-07-26-ai-usage-design.md` — 其中把私人网络与 `codex-nowin` 写成
   设计红线、且自称"唯一事实源"的部分，标注已被本文件取代。

### 测试

10. `tests/test_config.py` — 现在断言的是私人默认值，需改为通用默认值。
11. 新增覆盖：缺省 / 缺字段 / `proxy=""` / 纯空白的归一化；非空 URL 原样保留；
    非法类型给明确配置错误；`build_providers()` 确实把规范化结果传给三家。
12. `providers` 层：Claude 禁用代理时不读环境变量；Codex 禁用时子进程环境里
    大小写代理变量均被清除、显式启用时正确传入；Kimi `api_key_file=None` 不崩溃。
13. `tests/conftest.py` — 增加代理环境变量隔离，避免开发机/CI 的 `HTTP_PROXY`
    污染上述测试。

## 验收标准

- `pytest` 全绿
- 在**清空所有代理环境变量**的进程里，用默认配置（无 `config.toml`）构造三家 provider，
  确认其网络层均为直连
- 作者本机的 `config.toml` 仍能让三家取数正常（这是回归底线）

## 禁区

- 不改 `config.toml`（作者私人配置，已 gitignore）
- 不动 `server/poller.py`、`server/cache.py`、`web/` 与自启动相关的任何设计
- 不新增第三方依赖
- 不做任何删除操作
