# Changelog

English | [简体中文](docs/CHANGELOG.zh-CN.md) · [back to README](README.md)

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the version stays below 1.0.0, minor bumps may change behaviour.

## [0.3.0] — 2026-08-02

Fallout from a real incident: a proxy's TUN mode was switched on without the user noticing,
it took over the default route, and the Kimi provider timed out for hours. The provider
itself was fine — but recovering from it took far longer than it should have, and nothing in
the logs said why.

### Added

- **`python -m server.launch`, with a `--config <path>` option.** An explicitly given path
  is strict: a missing file, a non-regular or unreadable file, or a TOML parse error exits
  non-zero rather than silently falling back to the built-in defaults. Without `--config`
  it behaves exactly like the old `python -m server.main`.
- **Start-with-your-editor, with a switch** ([`deploy/vscode-hook.sh`](deploy/vscode-hook.sh)).
  Opening VS Code makes sure the daemon is up and brings the panel window out; closing the
  editor is left alone. `--enable` / `--disable` / `--status` control it, and the state *is*
  a flag file at `~/.config/ai-usage/vscode-hook.disabled` — nothing else is written to your
  system. Calling it repeatedly does not open a second window.

  There is deliberately no login/boot autostart. You are not necessarily working when the
  machine boots; you almost certainly are when you open an editor. If you do want it at
  login, each OS already has a mechanism for that (`shell:startup`, Login Items, a systemd
  unit) and wrapping another layer around them was not worth it.

### Changed

- **Default configuration is now generic — breaking if you relied on the built-in
  defaults.** The shipped defaults used to be one specific developer machine: a
  `http://127.0.0.1:7890` proxy for Claude and Codex, a private `codex-nowin` wrapper as
  the Codex command, and a `~/.config/shell/secrets.sh` key file for Kimi. A fresh clone
  on any other machine could reach none of the three providers. The defaults are now:
  direct connection for all three, `codex` as the Codex command, and no key file (Kimi
  falls back to `api_key` → `KIMI_API_KEY`). If your setup matched the old defaults,
  pin them explicitly in `config.toml` (see `config.example.toml`).
- **Proxy semantics are now two clean states, and "direct" really is direct.** A missing,
  empty, or whitespace-only `proxy` means direct connection; a non-empty URL means use
  that proxy. Previously a proxy could not be disabled at all: omitting it kept the
  built-in default via the config merge, an empty string crashed httpx, and even a
  normalized "no proxy" leaked — httpx read `HTTP_PROXY` & friends unless told not to,
  and the Codex app-server subprocess inherited the full parent environment. Now, with
  no proxy configured, the Claude client is created with `trust_env=False` and the Codex
  subprocess environment has the upper- and lowercase proxy variables (including
  `ALL_PROXY` and `NO_PROXY`) actively removed before any configured proxy is applied.
  Kimi also accepts an optional `proxy` for users on locked-down networks.
- **Backoff now starts at 60 seconds instead of 10 minutes.** The delay was
  `interval_seconds * 2 ** failures`, so with the default 5-minute interval a *single*
  failed poll pushed the next attempt 10 minutes out, and three failures reached the
  30-minute cap. A provider therefore stayed dark long after the network had recovered. It
  is now `first_retry_seconds * 2 ** (failures - 1)` — 60s, 120s, 240s, … capped by
  `max_backoff_seconds` — configurable via the new `poller.first_retry_seconds`.
- **Poll failures are logged at WARNING rather than INFO**, so they survive uvicorn's
  default log configuration. During the incident above, twelve hours of consecutive
  timeouts produced not one line in `journalctl`. The message still carries only the
  provider id and status: error text can contain URLs or credential fragments.
- **Importing `server.main` no longer has side effects.** It used to build the app at
  module level (`app = create_app()`); that now lives inside `main()`.
- **Error and auth-expired cards now say when the last attempt was** ("最后尝试 3 分钟前").
  Previously only `stale` cards carried a timestamp, so a failed card gave no hint whether
  it was one minute or one afternoon old. Worth noting the wording differs from the stale
  card's "数据来自 …": an error card has no data behind it, only an attempt.

### Fixed

- **Static assets are served with `Cache-Control: no-cache`.** The panel is a long-lived
  Chrome `--app` window, which serves CSS out of its in-memory cache and does not
  revalidate on an ordinary reload — so a style change could be live on disk, correct in
  the HTTP response, and still invisible in the widget until a hard reload. Over loopback
  there is nothing to gain from caching it.
- Corrected the `KimiProvider` docstring, which claimed the provider "禁止走代理" on the
  strength of `trust_env=False`. That flag only means environment proxy variables are
  ignored — a proxy running in TUN mode intercepts at the IP layer, where this module has
  no visibility. The network behaviour is unchanged; only the claim was wrong.

- **The pace tick is now lit.** It was a 2px line at `opacity: 0.34`, which made the one
  element the panel is built around the hardest thing on it to see — and it is meant to be
  compared against, at a glance, in a small window. It now has an opaque core that
  overshoots the bar by 2px at each end, a contrast rim so it stays crisp where it crosses
  the brand-coloured fill, and a two-layer halo that reads as a genuine glow in dark mode
  and as a lifted shadow in light mode. It stays ink-coloured rather than red: red already
  means "≥90% used" here, and a second red carrying a different meaning would blunt both.
  Note that `.bar` deliberately no longer sets `overflow: hidden` — it would clip both the
  overshoot and the halo. In dark mode the rim is kept deliberately faint: the core is
  already at maximum contrast there, and a heavy rim sits between core and halo and chokes
  the glow.

### Notes

- 100 offline tests (up from 87), including coverage of the new backoff schedule, the
  proxy semantics (normalization, env-var isolation, subprocess environment scrubbing),
  config-source resolution, and the strict `--config` semantics.
- A cross-platform login-autostart toggle was designed in full and then **dropped before
  implementation**; the spec is kept as an archive at
  [`docs/specs/2026-08-02-autostart-design.md`](docs/specs/2026-08-02-autostart-design.md).
  The complexity was out of proportion to the payoff: "touch nothing unless enabled" plus
  "always fully reversible", across four different startup mechanisms, needs ownership
  markers, conflict detection, a cross-process lock and step-wise compensation to hold up —
  and the thing it delivers was never what this project wanted. The archive records several
  verified facts worth reusing if it is ever revisited.
- The Chinese README was rewritten, and a Chinese changelog added
  ([docs/CHANGELOG.zh-CN.md](docs/CHANGELOG.zh-CN.md)).
- `docs/panel-dark.png` retaken with the lit tick. The new shot happens to catch Claude
  with its 5-hour bar short of the tick and its weekly bar past it — one account, two
  opposite readings — so the README now opens on that contrast instead of the old
  "everything has room to spare" shot.

## [0.2.0] — 2026-07-27

Tray interaction rework. The widget could get into states where clicking its tray icon did
nothing, and one of them was unrecoverable without killing the script.

### Added

- Tray icon responds to a **single left click** to show/hide the panel, matching what
  WeChat, QQ and most Windows tray apps do. Double click still works: Windows delivers a
  double click as *click + double-click + click*, and the redundant second click is
  swallowed, so the panel no longer shows and immediately hides again.
- `tests/tray-click-filter.ps1` covers the click-acceptance rules. It lifts the real
  `Test-AcceptClick` out of `tray-widget.ps1` via the PowerShell AST rather than testing a
  copied-out duplicate. Windows-only; the Linux CI job skips it.

### Fixed

- **Minimize button left the widget unreachable.** The window carries `WS_EX_TOOLWINDOW`, so
  it is not in the taskbar and a minimized window shrinks to a stub in a screen corner with
  no way back. Three Win32 details conspired here: a minimized window still reports
  `IsWindowVisible = true`, `SW_SHOW` does not clear the minimized flag (so "showing" it
  produced the stub again), and `GetWindowRect` returns the stub's geometry (measured
  159×27), which then got saved as the panel's remembered position. The watchdog now tucks a
  minimized window into the tray the same way the close button is handled, visibility checks
  account for `IsIconic`, redisplay uses `SW_RESTORE`, the position is not recorded while
  minimized, and a remembered rect smaller than 200px is discarded.
- **Clicks made during a cold start were replayed against the user.** Reopening after the
  title-bar X takes seconds while Chrome starts, and the UI thread is blocked throughout, so
  impatient extra clicks queue up and are all delivered the moment the panel appears —
  closing it again. Click filtering now keys off the message's *post* time
  (`GetMessageTime`), not the time it gets processed, and discards anything posted before the
  previous action finished.
- **Locating the panel window was unreliable.** `Get-Process().MainWindowTitle` reports only
  one window per process, which one depends on Z-order, and `MainWindowHandle` is `0` once
  the window is hidden. A miss during cold start meant a 20-second timeout and an error
  balloon. Window lookup now enumerates top-level windows directly (`EnumWindows`) and
  confirms the owning process belongs to the widget's own browser profile.

### Changed

- Visibility is read from the OS (`IsWindowVisible` + `IsIconic`) instead of a `$script:Visible`
  flag the script maintained itself, which could drift out of sync with the real window.
- Watchdog interval 800ms → 400ms, so a minimized window is tucked away before the stub is
  noticeable.

### Documentation

- Recorded that the widget runs on **Windows PowerShell 5.1** (`launcher.vbs` invokes
  `powershell.exe`) and why it deliberately does not use PowerShell 7: 5.1 ships with
  Windows and cannot be uninstalled out from under an autostart entry, and it starts ~100ms
  faster (measured 150–180ms vs 250–265ms). Every mechanism the widget uses works on both.
- Warned that PowerShell 7 writes files **without a BOM** by default. These scripts must stay
  UTF-8 *with* BOM or 5.1 decodes the Chinese comments as ANSI and fails to parse. Use
  `-Encoding utf8BOM` when rewriting them from `pwsh`.
- Warned that `install-widget.ps1` **copies** the script to `%LOCALAPPDATA%\ai-usage\`, so
  editing the repo does not affect the running widget until it is re-run.

## [0.1.0] — 2026-07-26

First public release: one panel for Claude, Codex and Kimi subscription quotas.

### Added

- Quota polling for all three providers, with an offline cache so the panel still renders
  when a provider is unreachable.
- Web panel served on loopback only, sized for a small always-visible window: per-provider
  cards, brand colours, a pace tick on every bar showing how much of the time window has
  elapsed, and extra credit pools shown as an amount.
- systemd user unit for running the server in the background.
- Zero-install Windows tray widget (`deploy/tray-widget.ps1`): a Chrome `--app` window
  restyled from the outside to stay out of the taskbar and Alt+Tab, with the title-bar close
  button translated into "tuck into the tray" instead of quitting.
- `deploy/install-widget.ps1` to install the widget and repoint an existing shortcut at it,
  launching through a VBS wrapper so no console window flashes.
- 68 offline tests, MIT licence, CI, and a bilingual README.

[0.3.0]: https://github.com/yxhuang/ai-usage/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/yxhuang/ai-usage/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/yxhuang/ai-usage/releases/tag/v0.1.0
