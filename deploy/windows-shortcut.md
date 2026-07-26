# 在 Windows 侧开成无地址栏小窗

WSL2 的 `localhost` 会自动转发到 Windows，所以 Windows 侧浏览器可以直接开面板；
用 `--app=` 模式打开就没有地址栏和标签栏，观感接近原生 widget。

前提：WSL 内的服务已经在跑（`deploy/install.sh` 装好的 systemd user unit 会开机自启）。

## 一、建快捷方式

在桌面右键 → 新建 → 快捷方式，目标填（端口按 `config.toml` 里的实际值，默认 8788）：

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --app=http://localhost:8788 --window-size=370,640
```

Edge 同理，把可执行文件换成
`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` 即可。

也可以用 PowerShell 直接建（脚本文件请存成 **带 BOM 的 UTF-8**，
否则 PowerShell 5.1 会按 ANSI 读，中文快捷方式名会变乱码）：

```powershell
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "AI 额度.lnk"))
$sc.TargetPath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$sc.Arguments  = '--app=http://localhost:8788 --window-size=370,640 --user-data-dir="%LOCALAPPDATA%\ai-usage-profile"'
$sc.IconLocation = "$($sc.TargetPath),0"
$sc.Save()
```

改个名字（如「AI 额度」），再进属性 → 更改图标挑一个顺眼的。

## 二、几个可选参数

| 参数 | 作用 |
|---|---|
| `--window-size=370,640` | **初始**窗口尺寸。页面加载后会按实际内容高度自适应，多退少补——不够高就撑开、有多余空白就收掉；浏览器不允许脚本改窗口尺寸时，才以这里给的值为准。宽度不会自适应，想调宽窄改这里 |
| `--window-position=x,y` | 固定开在屏幕某个角落 |
| `--user-data-dir=%LOCALAPPDATA%\ai-usage-profile` | 用独立的浏览器 profile，不受主浏览器窗口影响、也不会被误关 |

想让它开机自启：把快捷方式丢进 `shell:startup`（Win+R 输入即可打开该目录）。

## 三、改了 `--window-size` 却不生效

两个独立的坑，都会让改动看起来"没反应"：

1. **快捷方式是建好那一刻的快照**。这份文档里的值只是给你抄的模板，改文档不会动到桌面上
   已经建好的 `.lnk`——得回去改快捷方式属性里的「目标」，或者用下面的脚本重写。
2. **Chrome 会按 profile 记住 app 窗口的尺寸和位置**（存在 profile 的 `Preferences` 里）。
   一旦有了这条记录，`--window-size` 就被完全忽略，只有全新 profile 才认它。

所以要让新尺寸真正落地，得**同时**换掉快捷方式参数、和绕开那条记录。下面这段两件事
一起做。

**在哪跑**：Windows 侧的 PowerShell，**不是 WSL 终端**。`Win + X` → 「终端」或
「Windows PowerShell」，普通权限即可；确认提示符是 `PS C:\Users\...>` 而不是
`carls@...:~$`（终端里的 Ubuntu 标签页不行）。跑之前先**关掉已经开着的小窗**，
否则 Chrome 会拿旧记录复用窗口。

脚本里的快捷方式名是写死的，先确认真实名字对得上：

```powershell
Get-ChildItem ([Environment]::GetFolderPath("Desktop")) -Filter *.lnk | Select-Object Name
```

然后改 `$w`/`$h` 到想要的值，整段粘进去回车：

```powershell
$w = 370; $h = 640
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "AI 额度.lnk"
# profile 目录名带上宽度：等于换了个全新 profile，--window-size 才会被采纳。
# 旧目录原样留着，想回退把名字改回去即可。
$udd = "$env:LOCALAPPDATA\ai-usage-profile-$w"

# 不加这道判断的话，路径写错会默默生成一个打不开的空壳快捷方式
if (-not (Test-Path $lnk)) { throw "找不到快捷方式：$lnk" }

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.Arguments = "--app=http://localhost:8788 --window-size=$w,$h --user-data-dir=`"$udd`""
$sc.Save()

"已写入：" + $shell.CreateShortcut($lnk).Arguments
```

跑完双击快捷方式即可。首次用新 profile 打开时 Chrome 可能弹一次欢迎页，关掉就不再出现。

不想跑脚本也可以纯手工：右键快捷方式 → 属性 → 「目标」，把 `--window-size` 改成新值，
再给 `--user-data-dir` 的目录名加个后缀，效果一样。

代价是每换一次尺寸多留一个 profile 目录（几十 MB，`%LOCALAPPDATA%` 下自己清理）；
调定了就不用再动。

嫌麻烦也有个土办法：**直接拖窗口边缘调到顺眼为止**——Chrome 会把这个尺寸记下来，
下次打开就是它。缺点是换机器或重建 profile 后就没了，快捷方式里的值才是可移植的那份。

## 四、进阶：收进系统托盘，不占任务栏

想要「主界面像挂件停在桌面、任务栏上看不到、双击托盘图标才显隐」，用
[`tray-widget.ps1`](tray-widget.ps1)。零安装——不需要 Electron、AutoHotkey 之类的东西，
只用系统自带的 PowerShell + WinForms + `user32.dll`。

它做的事：启动一个 Chrome `--app` 窗口（界面就是本仓库这套，不另写一份），然后从外部改
它的窗口样式——去掉 `WS_CAPTION` 变无边框、加 `WS_EX_TOOLWINDOW` 从任务栏和 Alt+Tab
里摘掉、托盘双击走 `ShowWindow` 显隐。

跑（Windows 侧 PowerShell，普通权限）：

```powershell
powershell -ExecutionPolicy Bypass -File \\wsl$\Ubuntu\home\carls\=dev=\ai-usage\deploy\tray-widget.ps1
```

常用开关：

| 开关 | 作用 |
|---|---|
| `-Corner TopRight` | 开在哪个角（`TopLeft`/`TopRight`/`BottomLeft`/`BottomRight`/`None`），默认右上 |
| `-KeepFrame` | 保留系统标题栏。**无边框状态下窗口拖不动**，想随手挪位置就加它（任务栏照样是隐藏的） |
| `-Width` / `-Height` | 窗口尺寸，默认 370×640 |
| `-Port` | 面板端口，默认 8788 |

托盘图标右键有：显示/隐藏、四个角贴边、重启面板、退出。双击 = 显隐切换。

做成快捷方式开机自启的话，目标填
`powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File "<脚本路径>"`，
丢进 `shell:startup`。脚本启动后会把自己的控制台窗口藏掉，但**启动瞬间仍会闪一下黑框**。

要知道的代价：

- 这是**从外部操纵别的进程的窗口**，属于 hack。脚本必须常驻（托盘图标是它的，脚本一退
  图标就没）；Chrome 大版本升级若改了窗口结构，有失灵的可能。
- 它用的是独立 profile（`%LOCALAPPDATA%\ai-usage-widget`），跟第一节那个桌面快捷方式
  **互不干扰**，两种开法可以并存。
- 嫌它脆就换 Electron 自绘窗口那条路——那时网页代码一行都不用改，只是换个壳加载同一个
  `localhost:8788`。

## 五、打不开时按顺序查

1. WSL 里能不能开：`curl -s --noproxy '*' http://127.0.0.1:8788/api/summary`
2. 服务是否在跑：`systemctl --user status ai-usage`
3. 端口是否被占：`ss -ltn | grep 8788`（面板端口可在 `config.toml` 里改）
4. Windows 侧仍不通：重启一下 WSL 的 localhost 转发（管理员 PowerShell 跑 `wsl --shutdown` 再进）
