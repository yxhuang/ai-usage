# ai-usage

[![Release](https://img.shields.io/github/v/release/yxhuang/ai-usage?label=release)](https://github.com/yxhuang/ai-usage/releases/latest)
[![CI](https://github.com/yxhuang/ai-usage/actions/workflows/ci.yml/badge.svg)](https://github.com/yxhuang/ai-usage/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
![Local only](https://img.shields.io/badge/network-loopback%20only-brightgreen.svg)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/yxhuang/ai-usage/pulls)

**Three AI subscriptions. One small window. Know before you hit the wall.**

English | [简体中文](docs/README.zh-CN.md) · [Changelog](CHANGELOG.md)

If you pay for Claude Pro, ChatGPT Plus, and a Kimi plan, your quota lives in three
places and none of them tell you anything until you're already rate-limited. `ai-usage`
reads all three and puts them in one always-there panel.

<p align="center">
  <img src="docs/panel-dark.png" width="380"
       alt="The ai-usage panel in dark mode: three cards, one per provider. Claude Pro shows a 5-hour window at 67%, weekly at 22%, and a $22.98/$100 extra-credit pool at 23%. Codex Plus shows weekly at 12%. Kimi shows a 5-hour window at 3% and weekly at 2%. Each bar carries a glowing vertical tick marking how much of the time window has elapsed: Claude's 5-hour bar stops short of its tick, while its weekly bar has crossed it.">
</p>

> The UI is currently Chinese-only. See [Status](#status).

Look at Claude in that shot. The 5-hour bar stops short of its tick, so there is room to
keep working. The weekly bar has edged past its own tick, so that budget is running ahead
of schedule. Same account, same moment, two different answers — and the percentages alone
(67% and 22%) would have told you the opposite. That tick is the thing worth explaining.

## The pace tick

**This is the feature.** Everything else in this repo is plumbing that exists to draw it.

That glowing vertical line marks **how much of the time window has already elapsed**. You
read your usage bar against it:

```
weekly   ███████████████████░░░░░░░░░░░┃░░░░░░░░░   48% used, 78% of the week gone
                                                    left of the tick — room to spare

5-hour   █████████████┃██████████░░░░░░░░░░░░░░░░   61% used, 34% of the window gone
                                                    past the tick — burning it early
```

Left of the tick, you are spending slower than the window refills and you can keep going.
Past it, you are on track to run out before the reset — and the further past, the earlier
you hit the wall.

A plain percentage tells you what you have spent. The pace tick tells you whether you can
keep spending it that way, which is the question you actually have at 2pm on a Wednesday.

It is drawn as a lit needle that overshoots the bar at both ends, with a contrast rim so it
stays crisp where it crosses the coloured fill. That is the one loud thing in an otherwise
restrained panel, on purpose: a marker you have to squint at is a marker you will not use.
It stays ink-coloured rather than red, because red already means "≥90% used" here and a
second red with a different meaning would blunt both.

## What it does not do

It never sends your credentials anywhere except back to the vendor they belong to —
no third party ever sees them. How each provider authenticates differs: Claude reads
the OAuth file its CLI already wrote, Codex's app-server uses its own login state,
and Kimi uses the API key you configure. Credentials live in
memory. Nothing is written to disk except the usage numbers themselves, and the server
refuses to bind to anything but a loopback address.

It also costs you nothing to run: these are account endpoints, not inference. Polling
does not consume a single token of your quota.

## Quick start

```bash
git clone https://github.com/yxhuang/ai-usage && cd ai-usage
uv sync
uv run python -m server.launch   # open http://127.0.0.1:8788
```

That's it — no config file needed. Every provider that isn't logged in simply shows an
error card while the others keep working.

Config file somewhere else? Pass `--config`:

```bash
uv run python -m server.launch --config ~/.config/ai-usage/config.toml
```

To keep it running in the background (Linux, systemd user unit):

```bash
bash deploy/install.sh
journalctl --user -u ai-usage -f
```

## Requirements

- **Python 3.11+** and [uv](https://docs.astral.sh/uv/)
- At least one provider set up — the prerequisites differ per provider:

| Provider | Prerequisite |
|---|---|
| Claude | `claude` CLI installed and logged in. The panel reuses its OAuth token read-only. |
| Codex | `codex` CLI installed, logged in, and on your `PATH`. The panel spawns `codex app-server`; if that fails it falls back to local session logs. |
| Kimi | An `sk-kimi-*` API key. Kimi does **not** read the CLI's login state — set `providers.kimi.api_key` in `config.toml`, export `KIMI_API_KEY`, or point `providers.kimi.api_key_file` at a file containing it. |

The panel has no login flow of its own and never asks for a password.

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

## Start with your editor

There is deliberately no boot autostart. You are not necessarily working when the machine
boots; you almost certainly are when you open an editor. So that is the trigger: opening
VS Code brings the panel out, closing it is left alone.

[`deploy/vscode-hook.sh`](deploy/vscode-hook.sh) does three things — check the switch,
make sure the daemon is up (starting it if not), open the panel window. Calling it again
does not open a second window.

The switch lives in the panel: expand **设置** at the bottom and it is the first row. If the
hook isn't installed it says so rather than offering a toggle that does nothing.

The CLI reads and writes the same state:

```bash
deploy/vscode-hook.sh --status     # where things stand
deploy/vscode-hook.sh --disable    # off; later hook calls just exit
deploy/vscode-hook.sh --enable     # back on
```

The state *is* a flag file at `~/.config/ai-usage/vscode-hook.disabled` — delete it and the
hook is on again. Nothing else is written to your system.

**WSL + VS Code Remote.** Remote-WSL sources `~/.vscode-server/server-env-setup` before
starting its server, so add this there:

```sh
if [ -x ~/ai-usage/deploy/vscode-hook.sh ]; then
    setsid ~/ai-usage/deploy/vscode-hook.sh </dev/null >/dev/null 2>&1 &
fi
```

`setsid` is not optional. That file is *sourced*, so without detaching, the hook would hold
up VS Code's startup and get killed along with it on exit.

**Anywhere else.** VS Code has no official local startup hook; use an extension that can run
a command on startup, or skip the hook entirely and open the shortcut from the previous
section by hand. That is a perfectly normal way to use this — you just lose the "appears on
its own" part.

If login autostart is what you actually want, every OS already has it: drop the shortcut in
`shell:startup` on Windows, add it to Login Items on macOS, or use the systemd unit from
`deploy/install.sh` on Linux. This project does not wrap another layer around those;
[the reasoning is archived here](docs/specs/2026-08-02-autostart-design.md).

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
- `providers.<id>.proxy` — all three providers support an explicit proxy URL. The default
  (missing, empty, or whitespace-only) is a **direct connection**, and proxy environment
  variables
  (`HTTP_PROXY` etc.) are deliberately **ignored** in that case, so "direct" really means
  direct: the config value is the only way a proxy is used. One caveat in the other
  direction: a proxy running in **TUN mode** claims the default route and intercepts at the
  IP layer, so traffic still goes through it no matter what any provider is configured to
  do. If a provider suddenly times out while `curl` through the proxy port works, check
  `ip route get <target ip>` before suspecting the upstream API.
- `poller.first_retry_seconds` — how long to wait after the *first* failed poll, default
  `60`. Each further consecutive failure doubles it, capped by `max_backoff_seconds`.
  Successful polls always use `interval_seconds`.
- `providers.kimi` — the key falls back through `api_key` → environment variable → key file,
  first hit wins; without `api_key_file` the file source is skipped. The key file is parsed
  with a regex; it is **never** sourced or executed.
- `providers.codex.command` — the command used to spawn app-server (default `codex`), in
  case you wrap it.

## Security

- The server binds to loopback only, enforced in config validation. There is no option to
  expose it to your LAN by accident.
- Credentials are only ever sent back to the vendor they belong to, never to any third
  party. Claude reuses the OAuth file its CLI wrote (**read-only**), Codex's app-server
  uses its own login state, and Kimi uses the API key you configure. Credentials live in
  memory, are never written to disk, and are never logged. The on-disk cache
  (`data/cache.json`) holds usage numbers and nothing else.
- On error, logs record the provider name and exception type only — never the message or
  traceback, since those can carry a token-bearing URL.
- No account identity is read, displayed, or stored: no email, no org ID, no user ID.
- `config.toml` and `data/` are gitignored. If you put a key directly in `config.toml`,
  `chmod 600` it.

## Development

```bash
uv run pytest      # 115 tests; no network, no real credentials
```

Tests build responses and fake credentials with `httpx.MockTransport` and temp directories.
`tests/conftest.py` strips credential-shaped environment variables out of the test process —
that guard exists because of a real incident, where a test that didn't clean up its
environment printed a real key into an assertion failure. Tests should never be able to see
a real credential.

## Status

Early, and honest about it. The logic is covered by 115 offline tests, but it has only run on
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
