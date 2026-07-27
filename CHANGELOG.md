# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the version stays below 1.0.0, minor bumps may change behaviour.

## [Unreleased]

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

[Unreleased]: https://github.com/yxhuang/ai-usage/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/yxhuang/ai-usage/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/yxhuang/ai-usage/releases/tag/v0.1.0
