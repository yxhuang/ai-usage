# ai-usage

[![Release](https://img.shields.io/github/v/release/yxhuang/ai-usage?label=release)](https://github.com/yxhuang/ai-usage/releases/latest)
[![CI](https://github.com/yxhuang/ai-usage/actions/workflows/ci.yml/badge.svg)](https://github.com/yxhuang/ai-usage/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
![Local only](https://img.shields.io/badge/network-loopback%20only-brightgreen.svg)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/yxhuang/ai-usage/pulls)

**Three AI subscriptions. One small window. Know before you hit the wall.**

English | [简体中文](docs/README.zh-CN.md)

If you pay for Claude Pro, ChatGPT Plus, and a Kimi plan, your quota lives in three
places and none of them tell you anything until you're already rate-limited. `ai-usage`
reads all three and puts them in one always-there panel.

<p align="center">
  <img src="docs/panel-dark.png" width="380"
       alt="The ai-usage panel in dark mode: three cards, one per provider. Claude Pro shows a 5-hour window at 27%, weekly at 48%, and a $22.98/$100 extra-credit pool at 23%. Codex Plus shows weekly at 8%. Kimi shows a 5-hour window at 0% and weekly at 15%. Each bar carries a faint vertical tick marking how much of the time window has elapsed.">
</p>

> The UI is currently Chinese-only. See [Status](#status).

Every bar above sits to the left of its tick, which means all three have room to spare.
That tick is the thing worth explaining.

## The pace tick

That thin vertical line is the whole point. It marks **how much of the time window has
already elapsed**.

If your usage bar is to the left of it, you're spending slower than the window refills and
you can keep going. If the bar crosses it, you're on track to run out early. A plain
percentage tells you what you spent. The pace tick tells you whether you can keep spending
it that way — which is the question you actually have at 2pm on a Wednesday.

## What it does not do

It never sends your credentials anywhere. The daemon reads the token files your CLIs
already wrote, calls the vendor's own account-metadata endpoint, and keeps the token in
memory. Nothing is written to disk except the usage numbers themselves, and the server
refuses to bind to anything but a loopback address.

It also costs you nothing to run: these are account endpoints, not inference. Polling
does not consume a single token of your quota.

## Quick start

```bash
git clone https://github.com/yxhuang/ai-usage && cd ai-usage
uv sync
uv run python -m server.main     # open http://127.0.0.1:8788
```

That's it — no config file needed. Every provider that isn't logged in simply shows an
error card while the others keep working.

To keep it running in the background (systemd user unit, starts at boot):

```bash
bash deploy/install.sh
journalctl --user -u ai-usage -f
```

## Requirements

- **Python 3.11+** and [uv](https://docs.astral.sh/uv/)
- At least one of the three CLIs installed and logged in. The panel **reuses their existing
  credentials read-only** — it has no login flow of its own, and never asks for a password.

Runtime dependencies are `fastapi`, `uvicorn`, and `httpx`. The frontend is plain
HTML/CSS/JS: no build step, no node, no npm.

## Platforms

| | Status |
|---|---|
| Linux | Primary target. Developed and run on WSL2 Ubuntu. |
| macOS | Should work — same code paths, but not yet tested on a real Mac. Reports welcome. |
| Windows | Run the daemon **inside WSL**, then open the panel from Windows (see below). WSL2 forwards `localhost` automatically, so no extra networking setup. |

The daemon itself is plain Python and has no OS-specific code. The Windows-specific parts
are only about *displaying* the window.

## Making it a desktop widget

A browser window in `--app` mode already looks close to a native widget — no address bar,
no tabs. Two ways to run it on Windows, both documented in
[deploy/windows-shortcut.md](deploy/windows-shortcut.md):

**1. Just a shortcut.** One `chrome.exe --app=http://localhost:8788 --window-size=370,640`
and you have a clean little window.

**2. Live in the system tray** — [`deploy/tray-widget.ps1`](deploy/tray-widget.ps1).
The window stays out of the taskbar and Alt+Tab, the title bar's close and minimize buttons
both tuck it into the tray instead of quitting, and a single click on the tray icon toggles it
(a double click works too, without the show-then-hide flicker). It reopens right where you
left it.

**Zero install**: it's built on PowerShell + WinForms + `user32.dll`, all of which ship with
Windows. No Electron, no Tauri, no extra runtime — and it drives the same
`localhost:8788` page, so there is no second UI to maintain.
[`deploy/install-widget.ps1`](deploy/install-widget.ps1) will repoint an existing shortcut
at it.

Be aware of what that costs: the script manipulates a Chrome window from the outside, which
is a hack. It has to stay resident, and a major Chrome release that changes the window
structure could break it. The title bar can't be removed at all (Chrome draws its own) —
which turned out to be a feature, since without it the window can't be dragged.

## Where the numbers come from

| Provider | How | Notes |
|---|---|---|
| Claude | `GET api.anthropic.com/api/oauth/usage` | Reuses the claude CLI's OAuth token. Returns the 5-hour and weekly windows, plus the separately billed extra-credit pool. |
| Codex | Spawns `codex app-server`, calls `account/rateLimits/read` over JSON-RPC | Started on demand and shut down right after. If that fails, falls back to the most recent rate-limit snapshot in the local session logs, flagged `stale` with its timestamp. |
| Kimi | `GET api.kimi.com/coding/v1/usages` | Uses an `sk-kimi-*` API key. The response only carries absolute numbers; percentages are computed locally. |

Providers are fully independent: if one breaks, only that card shows an error and the rest
keep updating. Polling defaults to 300s, with exponential backoff per provider on failure
(capped at 30 minutes).

**These are unofficial endpoints.** None of them is a documented public API, and any of the
three vendors could change or remove theirs without notice. See the disclaimer below.

## Configuration

Everything is optional — see [config.example.toml](config.example.toml). Without a
`config.toml`, built-in defaults apply. The ones worth knowing:

- `server.port` — defaults to `8788`. `server.host` **only accepts loopback addresses**;
  anything else refuses to start.
- `providers.claude.proxy` — if you reach Anthropic through a proxy, set it here.
  **You must set it explicitly**: the daemon is a non-interactive process, doesn't read your
  shell config, and will not inherit proxy environment variables.
- `providers.kimi` — the key falls back through `api_key` → environment variable → key file,
  first hit wins. The key file is parsed with a regex; it is **never** sourced or executed.
- `providers.codex.command` — the command used to spawn app-server, in case you wrap it.

## Security

- The server binds to loopback only, enforced in config validation. There is no option to
  expose it to your LAN by accident.
- Credentials are **read-only reuse** of the files your CLIs already wrote. Tokens live in
  memory, are never written to disk, and are never logged. The on-disk cache
  (`data/cache.json`) holds usage numbers and nothing else.
- On error, logs record the provider name and exception type only — never the message or
  traceback, since those can carry a token-bearing URL.
- No account identity is read, displayed, or stored: no email, no org ID, no user ID.
- `config.toml` and `data/` are gitignored. If you put a key directly in `config.toml`,
  `chmod 600` it.

## Development

```bash
uv run pytest      # 68 tests; no network, no real credentials
```

Tests build responses and fake credentials with `httpx.MockTransport` and temp directories.
`tests/conftest.py` strips credential-shaped environment variables out of the test process —
that guard exists because of a real incident, where a test that didn't clean up its
environment printed a real key into an assertion failure. Tests should never be able to see
a real credential.

## Status

Early, and honest about it. The logic is covered by 68 offline tests, but it has only run on
one machine so far. If a provider's response shape differs on your account — a different
plan tier, a different region — a bug report with the (redacted) payload would genuinely
help.

**The interface is Chinese-only right now.** Labels come from both the frontend and the
provider layer, so English support means touching both — it's a wanted contribution, not a
hard problem. Open an issue if you'd use it.

Not planned for v1: history graphs, threshold alerts, a Tauri/Electron shell, OAuth token
auto-renewal, multi-user or remote access.

Released versions and what changed in each: [CHANGELOG.md](CHANGELOG.md). Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html); below 1.0.0, minor bumps may
change behaviour.

## Contributing

Issues and PRs welcome, especially a report from a machine that isn't mine, or a macOS
confirmation. If you touch the code, note that no absolute `/home/<user>` path should end up
in a commit.

## Disclaimer

`ai-usage` is a personal, unofficial tool. It is **not affiliated with, endorsed by, or
supported by** Anthropic, OpenAI, or Moonshot AI. Product names are used only to identify
the services it reads.

It relies on undocumented account-metadata endpoints that those vendors may change or remove
at any time, and it reads credential files that their CLIs manage. It only ever reads them,
and only sends them back to the vendor they belong to — but you run it at your own risk, and
you are responsible for staying within your provider's terms of service. Provided as-is,
without warranty. See [LICENSE](LICENSE).

## Acknowledgments

Built with help from the very assistants whose quota it tracks: Claude Code, Codex CLI, and
Kimi CLI. Design notes:
[docs/specs/2026-07-26-ai-usage-design.md](docs/specs/2026-07-26-ai-usage-design.md).

## License

Released under the [MIT License](LICENSE).

---

⭐ If this saved you from finding out about a rate limit the hard way, a star helps others
find it.
