<#
.SYNOPSIS
    把 ai-usage 面板变成桌面挂件：托盘图标控制显隐，窗口不占任务栏。

.DESCRIPTION
    零安装方案——不引入 Electron / AutoHotkey，只用 Windows 自带的 PowerShell +
    WinForms + user32.dll。做法是启动一个 Chrome --app 窗口（界面完全复用
    http://localhost:<Port> 那一套），再从外部改它的窗口样式：

      · 加 WS_EX_TOOLWINDOW        → 从任务栏和 Alt+Tab 里消失
      · ShowWindow SW_HIDE/SW_SHOW → 托盘单击切换显隐

    关于标题栏那两个按钮（X 和「最小化」）：**都拦不住**。Chrome 自绘标题栏，它们不走 WM_SYSCOMMAND，
    跨进程拦 WM_CLOSE 要往 Chrome 里注入 DLL，PowerShell 做不到、也不该做。所以这里
    改成「事后翻译」——看门狗发现窗口没了就把状态标成隐藏（脚本和托盘图标都留着），
    下次要显示时再懒加载重开一个，并还原关掉前的位置和尺寸。对你而言效果等同于
    最小化到托盘，唯一区别是再次打开有约 1 秒的冷启动，且页面会重新取一次数。
    彻底退出走托盘右键的「退出」。

    「最小化」按钮同样是事后翻译：窗口不在任务栏（TOOLWINDOW 就是这个代价），最小化后
    只会缩成屏幕角落一个小条，点它没用也没处调回来，所以看门狗一见 IsIconic 就把它收进
    托盘。要命的是最小化的窗口 IsWindowVisible 照样是 true、GetWindowRect 报的是那个小条
    的尺寸——所以显隐判断要连 IsIconic 一起看，位置更不能在那时候记，重新显示也必须
    SW_RESTORE（SW_SHOW 不清最小化标志，显出来还是那个小条）。

    托盘左键：**单击**即切换显隐（跟微信/QQ 一个手感）。双击的第二下会被吞掉，所以老习惯
    双击也照样能用，不会变成「显示又隐藏」闪一下。冷启动重开面板的那几秒里 UI 线程被占着、
    托盘没反应，这期间补点的击键同样作废——否则它们会在面板打开的瞬间集中送达，把刚开好的
    窗口又关回去。判据见 Test-AcceptClick，改它之前先跑 tests/tray-click-filter.ps1。

    ⚠️ 改完这个文件要重新部署才生效：install-widget.ps1 是把脚本**复制**到
    %LOCALAPPDATA%\ai-usage\，托盘跑的是那份副本，改仓库不会自动同步。重跑一次
    install-widget.ps1 即可（它会覆盖副本，快捷方式和图标都不用重设）。

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
using System.Collections.Generic;
using System.Text;
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
    // 最小化的窗口 IsWindowVisible 照样是 true，光看它会把「缩在左下角的小条」当成正常显示
    [DllImport("user32.dll")]
    public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();
    // 当前正在处理的这条消息是「什么时候投递的」，跟 Environment.TickCount 同基准。
    // 用它才能分辨「用户看到上一次结果之后点的」和「上一次还在忙时瞎点的」。
    [DllImport("user32.dll")]
    public static extern int GetMessageTime();
    [DllImport("kernel32.dll")]
    public static extern IntPtr GetConsoleWindow();

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }

    // 自己枚举顶层窗口按标题认人。别用 Process.MainWindowHandle：一个 chrome 进程可能
    // 挂着好几个窗口，.NET 只报「枚举到的第一个」，Z 序一变报的就换人；而且窗口一旦
    // 被 SW_HIDE，MainWindowHandle 直接是 0，隐藏状态下压根找不回自己的窗口。
    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextW(IntPtr hWnd, StringBuilder buf, int max);
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);

    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    public static IntPtr[] WindowsTitled(string prefix) {
        var hits = new List<IntPtr>();
        EnumWindows(delegate(IntPtr h, IntPtr l) {
            var sb = new StringBuilder(512);
            if (GetWindowTextW(h, sb, 512) > 0 &&
                sb.ToString().StartsWith(prefix, StringComparison.Ordinal)) { hits.Add(h); }
            return true;
        }, IntPtr.Zero);
        return hits.ToArray();
    }

    public static uint PidOf(IntPtr hWnd) { uint p; GetWindowThreadProcessId(hWnd, out p); return p; }
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
$SW_RESTORE       = 9
$SWP_NOSIZE       = 0x0001
$SWP_NOZORDER     = 0x0004
$SWP_NOACTIVATE   = 0x0010
$SWP_FRAMECHANGED = 0x0020

$Url = "http://localhost:$Port"
# 独立 profile：跟桌面快捷方式那份分开，两种开法可以并存互不干扰。
# 这个名字同时是「认领标记」——命令行里带它的浏览器进程才是挂件自己的，
# 找窗口和收摊时都靠它跟你日常的 Chrome 划清界限。
$WidgetTag = "ai-usage-widget"
$UserDataDir = Join-Path $env:LOCALAPPDATA $WidgetTag

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
$script:Chrome = $null
# 上一次显隐动作做完的时刻。之前用一个 $script:Visible 记忆量当真相，一旦跟窗口的实际
# 状态跑偏，切换就反着来；现在显隐一律问 IsWindowVisible，这个变量只用来挡「动作还没
# 做完时投递的点击」，不再参与状态判断。
$script:LastActionEndTick = [Environment]::TickCount

function Find-PanelWindow {
    # 按标题找，而不是按我们启动的那个 pid：Chrome 常把新窗口交给已有的浏览器进程，
    # Start-Process 拿到的 pid 可能立刻就退了，句柄得从窗口标题这边捞。
    #
    # 光靠标题还不够——万一你自己开的网页标题也叫这个，就会误伤（脚本会把它从任务栏摘掉、
    # 拿它当面板显隐）。所以命中之后再查一道：这窗口得属于带 ai-usage-widget 那个 profile
    # 的浏览器进程。这道校验只对已命中的候选做，不进轮询热路径。
    foreach ($h in [W32]::WindowsTitled($Title)) {
        $owner = [W32]::PidOf($h)
        $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId = $owner" -ErrorAction SilentlyContinue).CommandLine
        if ($cmd -and $cmd -like "*$WidgetTag*") { return $h }
    }
    return $null
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

# 显隐状态问系统要，不自己记：自己记的那份迟早跟实际跑偏。
# 最小化不算「显示」——那时窗口只剩左下角一个小条，用户的本意是把它收起来。
function Test-PanelVisible {
    return (Test-PanelAlive) -and [W32]::IsWindowVisible($script:Hwnd) -and
        (-not [W32]::IsIconic($script:Hwnd))
}

function Show-Panel {
    # 窗口可能已经被标题栏的 X 关掉了（那是真的关闭，见文件头说明），这时懒加载重开一个
    if (-not (Test-PanelAlive)) {
        try {
            Start-Panel
        } catch {
            $script:Notify.ShowBalloonTip(5000, "面板起不来", $_.Exception.Message,
                [System.Windows.Forms.ToolTipIcon]::Error)
            $script:LastActionEndTick = [Environment]::TickCount
            return
        }
    }
    # 最小化过的窗口必须 SW_RESTORE。SW_SHOW 只是「按当前状态显示」，最小化标志还在，
    # 显出来的仍是缩在屏幕角落那个小条——点多少次都一样，实测过。
    if ([W32]::IsIconic($script:Hwnd)) {
        [void][W32]::ShowWindow($script:Hwnd, $SW_RESTORE)
    } else {
        [void][W32]::ShowWindow($script:Hwnd, $SW_SHOW)
    }
    [void][W32]::SetForegroundWindow($script:Hwnd)
    $script:LastActionEndTick = [Environment]::TickCount
}

function Hide-Panel {
    if (Test-PanelAlive) { [void][W32]::ShowWindow($script:Hwnd, $SW_HIDE) }
    $script:LastActionEndTick = [Environment]::TickCount
}

function Switch-Panel {
    if (Test-PanelVisible) { Hide-Panel } else { Show-Panel }
}

# 记住窗口当前的位置和大小，供关掉后重开时还原——否则每次重开都跳回默认角落。
# 最小化时一定要跳过：那时 GetWindowRect 报的是缩略条的位置和尺寸（实测左下角
# 159×27 那么大一条），存下来就等于把「重开后的面板」定死成一个小条。
function Save-PanelRect {
    if (-not (Test-PanelAlive)) { return }
    if ([W32]::IsIconic($script:Hwnd)) { return }
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

    # 记下来的尺寸小得不像话就别用它——那多半是最小化时抓到的缩略条（实测 159×27），
    # 照着还原等于把面板钉死成一个小条。这种时候退回默认角落，至少是个能用的窗口。
    $r = $script:LastRect
    if ($r -and (($r.Right - $r.Left) -lt 200 -or ($r.Bottom - $r.Top) -lt 200)) {
        $script:LastRect = $null
        $r = $null
    }
    if ($r) {
        # 关掉前在哪就还回哪，连尺寸一起（用户可能自己拖过边）
        [void][W32]::SetWindowPos($script:Hwnd, [IntPtr]::Zero,
            $r.Left, $r.Top, ($r.Right - $r.Left), ($r.Bottom - $r.Top),
            ($SWP_NOZORDER -bor $SWP_NOACTIVATE))
    } else {
        Set-Corner $script:Hwnd $Corner
    }
}

function Stop-Panel {
    if ($script:Chrome -and -not $script:Chrome.HasExited) {
        try { $script:Chrome.CloseMainWindow() | Out-Null } catch {}
    }
    # CloseMainWindow 对已被交接给别的浏览器进程的窗口无效，兜底按 profile 精确清理，
    # 不碰用户其它的 Chrome 窗口
    Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe' OR Name = 'msedge.exe'" |
        Where-Object { $_.CommandLine -like "*$WidgetTag*" } |
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
$notify.Text = "AI 用量面板（单击显示 / 隐藏）"
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
        # 重启同样要好几秒，期间攒下的托盘点击一并作废
        $script:LastActionEndTick = [Environment]::TickCount
    })
$miExit = $menu.Items.Add("退出")
$miExit.Add_Click({ $script:Context.ExitThread() })

$notify.ContextMenuStrip = $menu

# 左键单击就显隐。两道过滤，判据都用消息的**投递**时刻（GetMessageTime）而不是处理时刻——
# 点击可能在队列里躺了好几秒才轮到，用处理时刻判断等于把这段等待抹掉了：
#
#   一、上一次动作还没做完时投递的点击，丢。面板被标题栏 X 关掉后再打开要走 Chrome 冷启动，
#       这几秒 UI 线程被占着、托盘毫无反应，用户自然会补点几下。这些点击不会消失，而是排队
#       等冷启动结束后集中送达（实测：处理器阻塞 1.5 秒期间投递的 3 条消息，解除阻塞后一条
#       不少全部执行）。不丢掉它们，就是面板刚打开又被自己关回去——「双击了面板不出来」正是
#       这么来的。
#   二、双击的第二下，丢。Windows 一次双击发的是「单击 + 双击 + 单击」，不去重的话双击就
#       变成显示又隐藏。DoubleClick 事件因此不再挂处理器，挂了等于多切一次。
$script:LastClickTick = [Environment]::TickCount - 10000

# 判据单独成函数，是为了能被 tests/tray-click-filter.ps1 原样抠出来验证——
# 它的行为不该靠肉眼复核，改动这段之前先跑那个测试。
function Test-AcceptClick([int]$sent) {
    if (($sent - $script:LastActionEndTick) -le 0) { return $false }
    if (($sent - $script:LastClickTick) -lt [System.Windows.Forms.SystemInformation]::DoubleClickTime) { return $false }
    $script:LastClickTick = $sent
    return $true
}

$notify.Add_MouseClick({
        param($sender, $e)
        if ($e.Button -ne [System.Windows.Forms.MouseButtons]::Left) { return }
        # 显隐做完时 Show-/Hide-Panel 自己会更新 LastActionEndTick
        if (Test-AcceptClick ([W32]::GetMessageTime())) { Switch-Panel }
    })

if (-not $serverUp) {
    $notify.ShowBalloonTip(5000, "服务没连上",
        "$Url 没响应，WSL 里的 ai-usage 服务可能没起来。起好之后从托盘菜单选「重启面板」。",
        [System.Windows.Forms.ToolTipIcon]::Warning)
}

# 看门狗：一是随时记住窗口位置（供关掉后重开还原），二是把「标题栏 X 被按下」
# 翻译成「收进托盘」——窗口没了不退出脚本，只把状态标成隐藏，等下次要显示时再懒加载重开。
$script:ClosedHintShown = $false
$watch = New-Object System.Windows.Forms.Timer
# 400ms：最小化后那个缩略条要尽快收掉，慢了会在屏幕角落杵着晃眼
$watch.Interval = 400
$watch.Add_Tick({
        if (Test-PanelAlive) {
            # 标题栏那个「最小化」跟 X 一样拦不住（Chrome 自绘），也只能事后翻译成「收进
            # 托盘」。不管的话窗口会缩成屏幕角落一个小条赖着不走——它不在任务栏（TOOLWINDOW
            # 就是这个代价），点它没用、也没地方能把它调回来。
            if ([W32]::IsIconic($script:Hwnd)) {
                if ([W32]::IsWindowVisible($script:Hwnd)) {
                    [void][W32]::ShowWindow($script:Hwnd, $SW_HIDE)
                }
                return
            }
            if ([W32]::IsWindowVisible($script:Hwnd)) { Save-PanelRect }
            return
        }
        if ($script:Hwnd -ne [IntPtr]::Zero) {
            # 窗口刚刚消失：X 被按了，或者 Chrome 崩了
            $script:Hwnd = [IntPtr]::Zero
            if (-not $script:ClosedHintShown) {
                $script:ClosedHintShown = $true
                $script:Notify.ShowBalloonTip(4000, "已收进托盘",
                    "点一下托盘图标可以再打开（约 1 秒冷启动）。要彻底退出，右键图标选「退出」。",
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
