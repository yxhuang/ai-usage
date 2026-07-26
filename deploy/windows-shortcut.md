# 在 Windows 侧开成无地址栏小窗

WSL2 的 `localhost` 会自动转发到 Windows，所以 Windows 侧浏览器可以直接开面板；
用 `--app=` 模式打开就没有地址栏和标签栏，观感接近原生 widget。

前提：WSL 内的服务已经在跑（`deploy/install.sh` 装好的 systemd user unit 会开机自启）。

## 一、建快捷方式

在桌面右键 → 新建 → 快捷方式，目标填（端口按 `config.toml` 里的实际值，默认 8788）：

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --app=http://localhost:8788 --window-size=400,700
```

Edge 同理，把可执行文件换成
`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` 即可。

也可以用 PowerShell 直接建（脚本文件请存成 **带 BOM 的 UTF-8**，
否则 PowerShell 5.1 会按 ANSI 读，中文快捷方式名会变乱码）：

```powershell
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "AI 额度.lnk"))
$sc.TargetPath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$sc.Arguments  = '--app=http://localhost:8788 --window-size=400,700 --user-data-dir="%LOCALAPPDATA%\ai-usage-profile"'
$sc.IconLocation = "$($sc.TargetPath),0"
$sc.Save()
```

改个名字（如「AI 额度」），再进属性 → 更改图标挑一个顺眼的。

## 二、几个可选参数

| 参数 | 作用 |
|---|---|
| `--window-size=400,700` | **初始**窗口尺寸。页面加载后会按实际内容高度自适应一次，把窗口调到刚好装下、不出滚动条；浏览器不允许脚本改窗口尺寸时，就以这里给的值为准 |
| `--window-position=x,y` | 固定开在屏幕某个角落 |
| `--user-data-dir=%LOCALAPPDATA%\ai-usage-profile` | 用独立的浏览器 profile，不受主浏览器窗口影响、也不会被误关 |

想让它开机自启：把快捷方式丢进 `shell:startup`（Win+R 输入即可打开该目录）。

## 三、打不开时按顺序查

1. WSL 里能不能开：`curl -s --noproxy '*' http://127.0.0.1:8788/api/summary`
2. 服务是否在跑：`systemctl --user status ai-usage`
3. 端口是否被占：`ss -ltn | grep 8788`（面板端口可在 `config.toml` 里改）
4. Windows 侧仍不通：重启一下 WSL 的 localhost 转发（管理员 PowerShell 跑 `wsl --shutdown` 再进）
