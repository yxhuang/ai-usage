<#
.SYNOPSIS
    验证托盘左键的两个判据（tray-widget.ps1 里的 Test-AcceptClick 和 Get-ClickAction）。

.DESCRIPTION
    Test-AcceptClick（哪些点击该受理）出过两次问题，值得单独测：

      · 单击必须受理——最早的版本只挂了 DoubleClick，单击毫无反应。
      · 双击的第二下必须丢弃——Windows 一次双击发的是「单击 + 双击 + 单击」，
        不去重就变成显示又隐藏，看着像没反应。
      · 上一次动作还没做完时投递的点击必须丢弃——冷启动重开面板要几秒，这期间
        用户补点的击键会排队，等面板打开后集中送达，把窗口又关回去。

    Get-ClickAction（受理之后该显示还是该隐藏）同样值得单独测：「系统认为窗口可见」
    不等于「用户看得见」——窗口被别的窗口完全遮挡时点一下反而把它藏得更深，
    要点两下才出来。

    测的是 tray-widget.ps1 里那份真代码：按函数名从源文件里把函数抠出来
    再加载，不抄第二份。

.NOTES
    只能在 Windows 侧的 PowerShell 里跑（要 System.Windows.Forms）；Linux CI 跳过。
    5.1 和 7 都能跑，实测两边全绿。但本文件同样必须是**带 BOM 的 UTF-8**——pwsh 写文件
    默认不带 BOM，用它改写会把 BOM 弄丢，5.1 再读就崩在中文上。

    powershell -ExecutionPolicy Bypass -File .\tests\tray-click-filter.ps1
#>

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms

$src = Join-Path (Split-Path $PSScriptRoot -Parent) "deploy\tray-widget.ps1"
if (-not (Test-Path $src)) { throw "找不到 tray-widget.ps1：$src" }

# 抠出被测函数。用 AST 定位，别拿正则去啃 PowerShell 语法。
$ast = [System.Management.Automation.Language.Parser]::ParseFile($src, [ref]$null, [ref]$null)
$fn = $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-AcceptClick'
    }, $true) | Select-Object -First 1
if (-not $fn) { throw "tray-widget.ps1 里没有 Test-AcceptClick——函数被改名了？测试要跟着更新" }
. ([scriptblock]::Create($fn.Extent.Text))

$DoubleClickTime = [System.Windows.Forms.SystemInformation]::DoubleClickTime
$failed = 0

function Check([string]$name, $expected, $actual) {
    if ($expected -eq $actual) {
        Write-Host "  PASS  $name"
    } else {
        Write-Host "  FAIL  $name（期望 $expected，实得 $actual）" -ForegroundColor Red
        $script:failed++
    }
}

# 每个用例自己摆好状态：LastActionEndTick = 上次动作做完的时刻，
# LastClickTick = 上次受理的点击的投递时刻，$sent = 本次点击的投递时刻。
function Reset([int]$actionEnd, [int]$lastClick) {
    $script:LastActionEndTick = $actionEnd
    $script:LastClickTick = $lastClick
}

Write-Host "DoubleClickTime = $DoubleClickTime ms"
Write-Host "Test-AcceptClick："

# 1. 闲着的时候单击 → 受理
Reset -actionEnd 10000 -lastClick 0
Check "闲时单击受理" $true (Test-AcceptClick 20000)

# 2. 紧跟着的第二下（双击的后半）→ 丢弃
Reset -actionEnd 10000 -lastClick 0
[void](Test-AcceptClick 20000)
Check "双击第二下丢弃" $false (Test-AcceptClick (20000 + [int]($DoubleClickTime / 2)))

# 3. 隔开双击判定时间后再单击 → 受理（连点两次要能开了再关）
Reset -actionEnd 10000 -lastClick 0
[void](Test-AcceptClick 20000)
Check "间隔后再单击受理" $true (Test-AcceptClick (20000 + $DoubleClickTime + 50))

# 4. 冷启动阻塞期间投递的点击 → 丢弃。
#    动作从 t=20000 一直做到 t=23000（Chrome 冷启动 3 秒），用户在 t=21000、t=22000
#    各补了一下；这两条要到 t=23000 之后才被送达处理，判据必须看投递时刻。
Reset -actionEnd 10000 -lastClick 0
[void](Test-AcceptClick 20000)
$script:LastActionEndTick = 23000     # 动作做完
Check "阻塞期间补点①丢弃" $false (Test-AcceptClick 21000)
Check "阻塞期间补点②丢弃" $false (Test-AcceptClick 22000)

# 5. 动作做完之后的点击 → 受理（不能把人一直挡在门外）
Check "动作结束后单击受理" $true (Test-AcceptClick 23500)

# 6. 边界：投递时刻正好等于动作结束时刻 → 丢弃（这一下必定发生在动作期间）
Reset -actionEnd 30000 -lastClick 0
Check "与动作同刻的点击丢弃" $false (Test-AcceptClick 30000)

# ---- Get-ClickAction：显隐判据的真值表 ----
# 这段判据修过一个真机确认的故障，值得单独测：面板压在别的窗口底下时
# （IsWindowVisible=True、没最小化，但用户完全看不见），点一下托盘不该去隐藏它。
$fn = $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Get-ClickAction'
    }, $true) | Select-Object -First 1
if (-not $fn) { throw "tray-widget.ps1 里没有 Get-ClickAction——函数被改名了？测试要跟着更新" }
. ([scriptblock]::Create($fn.Extent.Text))

Write-Host ""
Write-Host "Get-ClickAction："

# 穷举 16 种布尔组合。期望值在这里独立计算：活着 + 系统认为可见 + 没最小化 +
# 露在最上面，四条全真才算「显示着」→ 点一下该隐藏；其余都算「看不见」→ 显示。
# 不许拿 Get-ClickAction 自己算期望值——那就等于什么都没测。
foreach ($alive in @($true, $false)) {
    foreach ($visible in @($true, $false)) {
        foreach ($iconic in @($true, $false)) {
            foreach ($onTop in @($true, $false)) {
                $expect = if ($alive -and $visible -and (-not $iconic) -and $onTop) { 'Hide' } else { 'Show' }
                Check "alive=$alive visible=$visible iconic=$iconic onTop=$onTop → $expect" `
                    $expect (Get-ClickAction $alive $visible $iconic $onTop)
            }
        }
    }
}

# 回归用例：活着 + 系统认为可见 + 没最小化 + 但被完全遮挡（onTop=false）→ 显示。
# 对应的真实故障：面板压在别的窗口（比如编辑器）底下，系统状态一切正常但用户看不见，
# 旧判据点一下反而走了隐藏（毫无变化），要点两下才出来。
Check "被完全遮挡时点一下算显示（bug 回归）" 'Show' (Get-ClickAction $true $true $false $false)

# 门控用例，即便穷举已覆盖也单独钉死，防的是特定方向的误改：
# alive=false 时其余三灯全亮也必须显示——防止有人误删 alive 门控
Check "窗口没了点一下算显示（alive 门控）" 'Show' (Get-ClickAction $false $true $false $true)
# visible=false 时其余三灯全亮也必须显示——防止有人误删 visible 门控；
# GetWindowRect 失败回退 onTop=true 时真会出现这个组合
Check "系统认为不可见时点一下算显示（visible 门控）" 'Show' (Get-ClickAction $true $false $false $true)

Write-Host ""
if ($failed -gt 0) { Write-Host "$failed 项未通过" -ForegroundColor Red; exit 1 }
Write-Host "全部通过" -ForegroundColor Green
