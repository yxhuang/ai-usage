<#
.SYNOPSIS
    把 tray-widget.ps1 装到 Windows 侧，并把已有的快捷方式改成启动它。

.DESCRIPTION
    做三件事：

      1. 把 tray-widget.ps1 复制到 %LOCALAPPDATA%\ai-usage\。
         为什么要复制而不是直接指向 \\wsl$\...：那条 UNC 路径要求 WSL 已经在跑，
         而快捷方式（尤其开机自启那份）可能比 WSL 更早启动，指过去会直接失败，
         连"服务没连上"的提示都来不及弹。
      2. 生成一个 launcher.vbs。它只干一件事：以隐藏窗口的方式调起 PowerShell。
         直接让快捷方式调 powershell.exe 的话，无论加不加 -WindowStyle Hidden，
         启动瞬间都会闪一下黑框；走 VBS 才是真的一点都不闪。
      3. 把指定的快捷方式（默认找开始菜单里的）改成指向这个 VBS。

    只改快捷方式的目标和参数，**不动它的图标**，也不删除任何东西。改之前会把原来的
    目标打印出来，想还原自己抄回去即可。

.PARAMETER ShortcutName
    快捷方式的显示名（不含 .lnk）。会依次在开始菜单（当前用户）、开始菜单（所有用户）、
    桌面里找。

.PARAMETER ShortcutPath
    直接指定 .lnk 的完整路径，给上面找不到的情况兜底。

.PARAMETER Startup
    顺带在 shell:startup 里放一份，开机自启。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install-widget.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install-widget.ps1 -ShortcutName "AI 额度" -Startup

.NOTES
    在 Windows 侧的 PowerShell 里跑，普通权限即可。
    本文件必须存为 **带 BOM 的 UTF-8**，否则 PowerShell 5.1 按 ANSI 读，中文全乱码。
#>

[CmdletBinding()]
param(
    [string]$ShortcutName = "AI 额度",
    [string]$ShortcutPath,
    [string]$Source,
    [string]$InstallDir = "$env:LOCALAPPDATA\ai-usage",
    [string]$Arguments = "",
    [switch]$Startup
)

$ErrorActionPreference = 'Stop'

# ---------- 1. 复制脚本 ----------

if (-not $Source) { $Source = Join-Path $PSScriptRoot "tray-widget.ps1" }
if (-not (Test-Path $Source)) { throw "找不到 tray-widget.ps1：$Source（用 -Source 指定）" }

if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}
$target = Join-Path $InstallDir "tray-widget.ps1"
Copy-Item -Path $Source -Destination $target -Force
Write-Host "已复制脚本 → $target"

# ---------- 2. 生成无闪烁启动器 ----------

# VBS 里不写死路径，改成从自身位置推算——这样文件可以是纯 ASCII，
# 不用操心 WScript 读 .vbs 的编码（用户名含中文时写死路径会踩坑）。
$vbs = @'
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -ExecutionPolicy Bypass -NoProfile -File """ & here & "\tray-widget.ps1"" EXTRA_ARGS", 0, False
'@
$vbs = $vbs -replace 'EXTRA_ARGS', $Arguments
$vbsPath = Join-Path $InstallDir "launcher.vbs"
Set-Content -Path $vbsPath -Value $vbs -Encoding ASCII
Write-Host "已生成启动器 → $vbsPath"

# ---------- 3. 改快捷方式 ----------

function Find-Shortcut {
    if ($ShortcutPath) {
        if (-not (Test-Path $ShortcutPath)) { throw "找不到快捷方式：$ShortcutPath" }
        return $ShortcutPath
    }
    $roots = @(
        "$env:APPDATA\Microsoft\Windows\Start Menu\Programs",
        "$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
        [Environment]::GetFolderPath("Desktop")
    )
    foreach ($r in $roots) {
        $hit = Get-ChildItem -Path $r -Filter "$ShortcutName.lnk" -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    throw "在开始菜单和桌面都没找到「$ShortcutName.lnk」，用 -ShortcutPath 指定完整路径"
}

$lnk = Find-Shortcut
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)

Write-Host ""
Write-Host "找到快捷方式：$lnk"
Write-Host "  原目标：$($sc.TargetPath)"
Write-Host "  原参数：$($sc.Arguments)"
Write-Host "  （想还原就把上面两行填回属性里）"

$sc.TargetPath = "$env:SystemRoot\System32\wscript.exe"
$sc.Arguments = "`"$vbsPath`""
$sc.WorkingDirectory = $InstallDir
# 图标不动：保留你自己挑过的那个
$sc.Save()

Write-Host ""
Write-Host "已改为：wscript.exe `"$vbsPath`""

# ---------- 4. 可选：开机自启 ----------

if ($Startup) {
    $startupDir = [Environment]::GetFolderPath("Startup")
    Copy-Item -Path $lnk -Destination (Join-Path $startupDir "$ShortcutName.lnk") -Force
    Write-Host "已放入开机启动：$startupDir"
}

Write-Host ""
Write-Host "完成。双击快捷方式试一下——不会有黑框闪过，托盘出图标即为成功。"
