<#
.SYNOPSIS
    把 ai-usage 面板变成桌面挂件：托盘图标控制显隐，窗口不占任务栏。

.DESCRIPTION
    零安装方案——不引入 Electron / AutoHotkey，只用 Windows 自带的 PowerShell +
    WinForms + user32.dll。做法是启动一个 Chrome --app 窗口（界面完全复用
    http://localhost:<Port> 那一套），再从外部改它的窗口样式：

      · 加 WS_EX_TOOLWINDOW        → 从任务栏和 Alt+Tab 里消失
      · ShowWindow SW_HIDE/SW_SHOW → 托盘双击切换显隐

    关于标题栏那个 X：**拦不住**。Chrome 自绘标题栏，它的关闭按钮不走 WM_SYSCOMMAND，
    跨进程拦 WM_CLOSE 要往 Chrome 里注入 DLL，PowerShell 做不到、也不该做。所以这里
    改成「事后翻译」——看门狗发现窗口没了就把状态标成隐藏（脚本和托盘图标都留着），
    下次要显示时再懒加载重开一个，并还原关掉前的位置和尺寸。对你而言效果等同于
    最小化到托盘，唯一区别是再次打开有约 1 秒的冷启动，且页面会重新取一次数。
    彻底退出走托盘右键的「退出」。

    代价（选这条路就要接受的）：这是从外部操纵别的进程的窗口，属于 hack。脚本必须
    常驻（托盘图标是它的，脚本退出图标就没）；Chrome 大版本升级若改了窗口结构有可能
    失灵。真嫌脆，再换 Electron 自绘窗口那条路，届时网页代码一行都不用改。

.PARAMETER Corner
    开在哪个屏幕角落。None = 不管位置，用 Chrome 自己记住的。

.PARAMETER Frameless
    尝试砍掉标题栏。默认不开：实测 Chrome 自绘标题栏，砍了没效果；而且真砍掉就没有
    拖拽区、窗口挪不动了。留这个开关是给标题栏由系统绘制的浏览器/版本。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\tray-widget.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\tray-widget.ps1 -Corner BottomRight

.NOTES
    本文件必须存为 **带 BOM 的 UTF-8**，否则 PowerShell 5.1 按 ANSI 读，中文全乱码。
#>

[CmdletBinding()]
param(
    [int]$Port = 8788,
    [string]$Title = "AI 用量面板",
    [int]$Width = 370,
    [int]$Height = 640,
    [ValidateSet('TopRight', 'BottomRight', 'TopLeft', 'BottomLeft', 'None')]
    [string]$Corner = 'TopRight',
    [int]$Margin = 12,
    [switch]$Frameless,
    [string]$ChromePath
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class W32 {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern int GetWindowLong(IntPtr hWnd, int nIndex);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern int SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter,
        int X, int Y, int cx, int cy, uint uFlags);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool IsWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();
    [DllImport("kernel32.dll")]
    public static extern IntPtr GetConsoleWindow();

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
}
'@

# ---------- 常量 ----------

$GWL_STYLE        = -16
$GWL_EXSTYLE      = -20
$WS_CAPTION       = 0x00C00000
$WS_EX_TOOLWINDOW = 0x00000080
$WS_EX_APPWINDOW  = 0x00040000
$SW_HIDE          = 0
$SW_SHOW          = 5
$SWP_NOSIZE       = 0x0001
$SWP_NOZORDER     = 0x0004
$SWP_NOACTIVATE   = 0x0010
$SWP_FRAMECHANGED = 0x0020

$Url = "http://localhost:$Port"
# 独立 profile：跟桌面快捷方式那份分开，两种开法可以并存互不干扰
$UserDataDir = Join-Path $env:LOCALAPPDATA "ai-usage-widget"

# 让 WorkingArea 和 SetWindowPos 用同一套（物理像素）坐标，
# 否则在 125% 缩放下贴边会偏。必须在碰任何窗口之前调。
[void][W32]::SetProcessDPIAware()

# ---------- 找 Chrome ----------

function Resolve-Chrome {
    if ($ChromePath) {
        if (-not (Test-Path $ChromePath)) { throw "指定的 Chrome 不存在：$ChromePath" }
        return $ChromePath
    }
    $candidates = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    throw "没找到 Chrome 或 Edge，用 -ChromePath 手动指定"
}

# ---------- 托盘图标：三段弧对应三家，颜色跟面板里的进度条一致 ----------

function New-TrayIcon {
    $bmp = New-Object System.Drawing.Bitmap 32, 32
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.Clear([System.Drawing.Color]::Transparent)
    $arcs = @(
        @{ Color = '#d97757'; Start = 135 },  # Claude 珊瑚橙
        @{ Color = '#10a37f'; Start = 255 },  # Codex 青绿
        @{ Color = '#1a73e8'; Start = 15  }   # Kimi 蓝
    )
    foreach ($a in $arcs) {
        $pen = New-Object System.Drawing.Pen(
            [System.Drawing.ColorTranslator]::FromHtml($a.Color), 5)
        $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $g.DrawArc($pen, 5, 5, 22, 22, $a.Start, 90)
        $pen.Dispose()
    }
    $g.Dispose()
    # FromHandle 拿到的图标句柄由这个进程持有到退出，托盘图标只建一次，不用管回收
    $icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
    $bmp.Dispose()
    return $icon
}

# ---------- 窗口操作 ----------

$script:Hwnd = [IntPtr]::Zero
$script:Visible = $true
$script:Chrome = $null

function Find-PanelWindow {
    # 按标题找，而不是按我们启动的那个 pid：Chrome 常把新窗口交给已有的浏览器进程，
    # Start-Process 拿到的 pid 可能立刻就退了，句柄得从窗口标题这边捞。
    $procName = [System.IO.Path]::GetFileNameWithoutExtension($script:ExePath)
    Get-Process -Name $procName -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like "$Title*" } |
        Select-Object -First 1 -ExpandProperty MainWindowHandle
}

function Set-WidgetStyle([IntPtr]$h) {
    # 任务栏/Alt+Tab：改 WS_EX_TOOLWINDOW 必须在窗口隐藏状态下做，否则不生效
    [void][W32]::ShowWindow($h, $SW_HIDE)
    $ex = [W32]::GetWindowLong($h, $GWL_EXSTYLE)
    $ex = ($ex -bor $WS_EX_TOOLWINDOW) -band (-bnot $WS_EX_APPWINDOW)
    [void][W32]::SetWindowLong($h, $GWL_EXSTYLE, $ex)

    # 标题栏：实测当前 Chrome 是自绘标题栏，砍 WS_CAPTION 对它无效（窗口照旧带栏）。
    # 保留标题栏反而是好事——没它就没有拖拽区，窗口挪不动。所以默认不砍，
    # 留个开关给标题栏是系统画的那些浏览器/版本。
    if ($Frameless) {
        $st = [W32]::GetWindowLong($h, $GWL_STYLE)
        [void][W32]::SetWindowLong($h, $GWL_STYLE, $st -band (-bnot $WS_CAPTION))
    }

    [void][W32]::ShowWindow($h, $SW_SHOW)
    # 通知窗口重算非客户区，不然边框残影还在
    [void][W32]::SetWindowPos($h, [IntPtr]::Zero, 0, 0, 0, 0,
        ($SWP_NOSIZE -bor $SWP_NOZORDER -bor $SWP_NOACTIVATE -bor $SWP_FRAMECHANGED))
}

function Set-Corner([IntPtr]$h, [string]$where) {
    if ($where -eq 'None') { return }
    $rect = New-Object W32+RECT
    if (-not [W32]::GetWindowRect($h, [ref]$rect)) { return }
    $w = $rect.Right - $rect.Left
    $ht = $rect.Bottom - $rect.Top
    $area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea

    switch ($where) {
        'TopLeft'     { $x = $area.Left + $Margin;          $y = $area.Top + $Margin }
        'TopRight'    { $x = $area.Right - $w - $Margin;    $y = $area.Top + $Margin }
        'BottomLeft'  { $x = $area.Left + $Margin;          $y = $area.Bottom - $ht - $Margin }
        'BottomRight' { $x = $area.Right - $w - $Margin;    $y = $area.Bottom - $ht - $Margin }
    }
    [void][W32]::SetWindowPos($h, [IntPtr]::Zero, $x, $y, 0, 0,
        ($SWP_NOSIZE -bor $SWP_NOZORDER -bor $SWP_NOACTIVATE))
}

function Test-PanelAlive {
    return ($script:Hwnd -ne [IntPtr]::Zero) -and [W32]::IsWindow($script:Hwnd)
}

function Show-Panel {
    # 窗口可能已经被标题栏的 X 关掉了（那是真的关闭，见文件头说明），这时懒加载重开一个
    if (-not (Test-PanelAlive)) {
        try {
            Start-Panel
        } catch {
            $script:Notify.ShowBalloonTip(5000, "面板起不来", $_.Exception.Message,
                [System.Windows.Forms.ToolTipIcon]::Error)
            return
        }
    }
    [void][W32]::ShowWindow($script:Hwnd, $SW_SHOW)
    [void][W32]::SetForegroundWindow($script:Hwnd)
    $script:Visible = $true
}

function Hide-Panel {
    if (-not (Test-PanelAlive)) { $script:Visible = $false; return }
    [void][W32]::ShowWindow($script:Hwnd, $SW_HIDE)
    $script:Visible = $false
}

function Switch-Panel {
    if ($script:Visible) { Hide-Panel } else { Show-Panel }
}

# 记住窗口当前的位置和大小，供关掉后重开时还原——否则每次重开都跳回默认角落
function Save-PanelRect {
    if (-not (Test-PanelAlive)) { return }
    $r = New-Object W32+RECT
    if ([W32]::GetWindowRect($script:Hwnd, [ref]$r)) { $script:LastRect = $r }
}

# ---------- 启动面板 ----------

function Start-Panel {
    # 不要用 $args 当变量名——那是 PowerShell 的自动变量
    $chromeArgs = @(
        "--app=$Url",
        "--window-size=$Width,$Height",
        "--user-data-dir=$UserDataDir",
        "--no-first-run",
        "--no-default-browser-check"
    )
    $script:Chrome = Start-Process -FilePath $script:ExePath -ArgumentList $chromeArgs -PassThru

    # 等窗口出现。Chrome 冷启动 + 页面首帧，给到 20 秒
    $deadline = (Get-Date).AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 300
        $h = Find-PanelWindow
    } while (-not $h -and (Get-Date) -lt $deadline)

    if (-not $h) { throw "20 秒内没等到标题以「$Title」开头的窗口，面板可能没起来" }
    $script:Hwnd = [IntPtr]$h
    Set-WidgetStyle $script:Hwnd

    if ($script:LastRect) {
        # 关掉前在哪就还回哪，连尺寸一起（用户可能自己拖过边）
        $r = $script:LastRect
        [void][W32]::SetWindowPos($script:Hwnd, [IntPtr]::Zero,
            $r.Left, $r.Top, ($r.Right - $r.Left), ($r.Bottom - $r.Top),
            ($SWP_NOZORDER -bor $SWP_NOACTIVATE))
    } else {
        Set-Corner $script:Hwnd $Corner
    }
    $script:Visible = $true
}

function Stop-Panel {
    if ($script:Chrome -and -not $script:Chrome.HasExited) {
        try { $script:Chrome.CloseMainWindow() | Out-Null } catch {}
    }
    # CloseMainWindow 对已被交接给别的浏览器进程的窗口无效，兜底按 profile 精确清理，
    # 不碰用户其它的 Chrome 窗口
    Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe' OR Name = 'msedge.exe'" |
        Where-Object { $_.CommandLine -like "*ai-usage-widget*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

# ---------- 主流程 ----------

$script:ExePath = Resolve-Chrome

# 服务没起来就先提醒一句，页面会是错误页
try {
    Invoke-WebRequest -Uri "$Url/api/summary" -TimeoutSec 3 -UseBasicParsing | Out-Null
    $serverUp = $true
} catch {
    $serverUp = $false
}

try {
    Start-Panel
} catch {
    # 起窗口失败就把可能已经拉起来的 chrome 收干净，别留个孤儿进程
    Stop-Panel
    throw
}

# 藏掉自己的控制台窗口（用 -WindowStyle Hidden 启动仍会闪一下，这里补一刀）
$console = [W32]::GetConsoleWindow()
if ($console -ne [IntPtr]::Zero) { [void][W32]::ShowWindow($console, $SW_HIDE) }

$script:Notify = New-Object System.Windows.Forms.NotifyIcon
$notify = $script:Notify
$notify.Icon = New-TrayIcon
$notify.Text = "AI 用量面板（双击显示 / 隐藏）"
$notify.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$miToggle = $menu.Items.Add("显示 / 隐藏")
$miToggle.Add_Click({ Switch-Panel })
[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))

foreach ($c in @(
    @{ Text = "贴左上"; Value = 'TopLeft' },
    @{ Text = "贴右上"; Value = 'TopRight' },
    @{ Text = "贴左下"; Value = 'BottomLeft' },
    @{ Text = "贴右下"; Value = 'BottomRight' })) {
    $item = $menu.Items.Add($c.Text)
    # 闭包里要用当前这轮的值，GetNewClosure 固化一份
    $item.Add_Click({ Set-Corner $script:Hwnd $c.Value; Show-Panel }.GetNewClosure())
}

[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))
$miRestart = $menu.Items.Add("重启面板")
$miRestart.Add_Click({
        Save-PanelRect
        Stop-Panel
        $script:Hwnd = [IntPtr]::Zero
        Start-Sleep -Milliseconds 500
        try {
            Start-Panel
        } catch {
            $script:Notify.ShowBalloonTip(5000, "面板起不来", $_.Exception.Message,
                [System.Windows.Forms.ToolTipIcon]::Error)
        }
    })
$miExit = $menu.Items.Add("退出")
$miExit.Add_Click({ $script:Context.ExitThread() })

$notify.ContextMenuStrip = $menu
$notify.Add_DoubleClick({ Switch-Panel })

if (-not $serverUp) {
    $notify.ShowBalloonTip(5000, "服务没连上",
        "$Url 没响应，WSL 里的 ai-usage 服务可能没起来。起好之后从托盘菜单选「重启面板」。",
        [System.Windows.Forms.ToolTipIcon]::Warning)
}

# 看门狗：一是随时记住窗口位置（供关掉后重开还原），二是把「标题栏 X 被按下」
# 翻译成「收进托盘」——窗口没了不退出脚本，只把状态标成隐藏，等下次要显示时再懒加载重开。
$script:ClosedHintShown = $false
$watch = New-Object System.Windows.Forms.Timer
$watch.Interval = 800
$watch.Add_Tick({
        if (Test-PanelAlive) {
            if ($script:Visible) { Save-PanelRect }
            return
        }
        if ($script:Visible) {
            # 窗口刚刚消失：X 被按了，或者 Chrome 崩了
            $script:Visible = $false
            $script:Hwnd = [IntPtr]::Zero
            if (-not $script:ClosedHintShown) {
                $script:ClosedHintShown = $true
                $script:Notify.ShowBalloonTip(4000, "已收进托盘",
                    "双击托盘图标可以再打开。要彻底退出，右键图标选「退出」。",
                    [System.Windows.Forms.ToolTipIcon]::Info)
            }
        }
    })
$watch.Start()

$script:Context = New-Object System.Windows.Forms.ApplicationContext
try {
    [System.Windows.Forms.Application]::Run($script:Context)
} finally {
    $watch.Stop()
    $notify.Visible = $false
    $notify.Dispose()
    Stop-Panel
}
