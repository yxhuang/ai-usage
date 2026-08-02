#!/usr/bin/env bash
# 跟随编辑器启动：打开 VSCode 时把面板带出来，关掉编辑器就不管了。
#
# 装法和各系统的差异见 README 的〈跟随编辑器启动〉一节。核心想法是不做开机自启——
# 开机时你未必要干活，而打开编辑器基本等于要干活了。
#
# 用法：
#   vscode-hook.sh            按需拉起 daemon 并打开面板窗口（供编辑器的 hook 调用）
#   vscode-hook.sh --status   查看开关状态
#   vscode-hook.sh --disable  关掉（之后 hook 被调用也直接退出）
#   vscode-hook.sh --enable   开回来
set -u

FLAG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/ai-usage"
FLAG="$FLAG_DIR/vscode-hook.disabled"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- 开关 ---------------------------------------------------------------

case "${1:-}" in
    --status)
        if [ -e "$FLAG" ]; then
            echo "跟随编辑器启动：已关闭（$FLAG）"
        else
            echo "跟随编辑器启动：已开启"
        fi
        exit 0
        ;;
    --disable)
        mkdir -p "$FLAG_DIR" && : > "$FLAG"
        echo "已关闭。删掉 $FLAG 或跑 $0 --enable 可以开回来。"
        exit 0
        ;;
    --enable)
        # 只动这一个由本脚本创建的标志文件
        [ -e "$FLAG" ] && rm -f "$FLAG"
        echo "已开启。"
        exit 0
        ;;
    "") ;;
    *)
        echo "未知参数：$1（可用：--status / --enable / --disable）" >&2
        exit 2
        ;;
esac

[ -e "$FLAG" ] && exit 0

# --- 端口 ---------------------------------------------------------------

# 配置里可能改过端口，读不出来就用默认值，不要因此卡住启动
PORT="${AI_USAGE_PORT:-}"
if [ -z "$PORT" ]; then
    PORT="$(cd "$PROJECT_DIR" && python3 -c \
        'from server.config import load_config; print(load_config().server.port)' \
        2>/dev/null || echo 8788)"
fi

panel_up() {
    curl -sf -o /dev/null --max-time 1 --noproxy '*' "http://127.0.0.1:$PORT/" 2>/dev/null
}

# --- 确保 daemon 在跑 ---------------------------------------------------

if ! panel_up; then
    if command -v systemctl >/dev/null 2>&1 \
       && systemctl --user list-unit-files ai-usage.service >/dev/null 2>&1; then
        systemctl --user start ai-usage.service >/dev/null 2>&1
    else
        # 没装 systemd unit 就直接起一个，脱离编辑器的进程组，免得关窗口时被一起收走
        if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
            PY="$PROJECT_DIR/.venv/bin/python"
        else
            PY="$(command -v python3 || echo python3)"
        fi
        ( cd "$PROJECT_DIR" && setsid "$PY" -m server.launch \
            </dev/null >/dev/null 2>&1 & ) 2>/dev/null
    fi
fi

# daemon 起来要几秒，抢在前面开窗只会先看到一屏连接失败
for _ in $(seq 1 60); do
    panel_up && break
    sleep 1
done
panel_up || exit 0

# --- 开窗 ---------------------------------------------------------------

URL="http://localhost:$PORT"
WIN_ARGS="--app=$URL --window-size=370,640"

# 已经开着就不再开一个。用锁文件记下自己拉起的浏览器 pid——比按窗口标题找可靠，
# 也不依赖任何平台专有的进程查询。
RUN_DIR="${XDG_RUNTIME_DIR:-/tmp}"
PIDFILE="$RUN_DIR/ai-usage-panel-window.pid"
if [ -r "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
    exit 0
fi

open_window() {
    case "$(uname -s)" in
        Darwin)
            for app in "Google Chrome" "Microsoft Edge" "Chromium"; do
                if [ -d "/Applications/$app.app" ]; then
                    open -na "$app" --args $WIN_ARGS && return 0
                fi
            done
            open "$URL" && return 0
            ;;
        Linux)
            # WSL：优先用装好的托盘挂件（deploy/install-widget.ps1 生成的那个），
            # 它比裸浏览器窗口好用；没有就退回 Windows 侧浏览器。
            if grep -qi microsoft /proc/version 2>/dev/null; then
                local wscript='/mnt/c/Windows/System32/wscript.exe'
                local localapp vbs
                localapp="$(cd /mnt/c && cmd.exe /c 'echo %LOCALAPPDATA%' 2>/dev/null | tr -d '\r\n')"
                if [ -n "$localapp" ]; then
                    vbs="$localapp\\ai-usage\\launcher.vbs"
                    # 从 UNC 路径调 Windows 程序会告警并把工作目录踢到 C:\Windows
                    if [ -x "$wscript" ] && (cd /mnt/c && "$wscript" "$vbs" 2>/dev/null); then
                        return 0
                    fi
                fi
                (cd /mnt/c && cmd.exe /c start "" "$URL") 2>/dev/null && return 0
            fi
            for b in google-chrome chromium chromium-browser microsoft-edge brave-browser; do
                if command -v "$b" >/dev/null 2>&1; then
                    setsid "$b" $WIN_ARGS </dev/null >/dev/null 2>&1 &
                    echo $! > "$PIDFILE"
                    return 0
                fi
            done
            command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL" >/dev/null 2>&1 && return 0
            ;;
    esac
    return 1
}

if ! open_window; then
    echo "ai-usage: daemon 已在 $URL 运行，但没找到能开窗的浏览器，请手动打开。" >&2
fi
