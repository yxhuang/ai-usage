#!/usr/bin/env bash
# 安装 / 更新 ai-usage 的 systemd user unit 并启动服务。
# 不需要 sudo：全部在用户级 systemd 里完成。
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$PROJECT_DIR/deploy/ai-usage.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/ai-usage.service"

# --- 找 uv ---
UV_BIN="$(command -v uv || true)"
[ -z "$UV_BIN" ] && [ -x "$HOME/.local/bin/uv" ] && UV_BIN="$HOME/.local/bin/uv"
if [ -z "$UV_BIN" ]; then
    echo "✗ 找不到 uv。先装：curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

# --- 前置检查 ---
if ! systemctl --user is-system-running >/dev/null 2>&1; then
    echo "✗ 用户级 systemd 不可用。WSL 需在 /etc/wsl.conf 里设 [boot] systemd=true 后重启发行版。" >&2
    exit 1
fi

if [ ! -d "$PROJECT_DIR/.venv" ]; then
    echo "→ 未发现 .venv，先建虚拟环境…"
    (cd "$PROJECT_DIR" && "$UV_BIN" sync)
fi

# 读出配置里的端口（没有 config.toml 就用默认值），仅用于末尾提示
PORT="$(cd "$PROJECT_DIR" && "$UV_BIN" run --no-sync python -c \
    'from server.config import load_config; print(load_config().server.port)' 2>/dev/null || echo 8788)"

# --- 生成 unit ---
mkdir -p "$UNIT_DIR"
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    -e "s|__UV_BIN__|$UV_BIN|g" \
    "$TEMPLATE" > "$UNIT_PATH"
echo "→ 已写入 $UNIT_PATH"

# --- 开机自启（无需登录）---
if [ "$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null || echo no)" != "yes" ]; then
    echo "! 未开启 linger：注销后服务会停。开启需要 sudo，请自行执行："
    echo "    sudo loginctl enable-linger $USER"
fi

systemctl --user daemon-reload
systemctl --user enable --now ai-usage.service
systemctl --user restart ai-usage.service

sleep 2
if systemctl --user is-active --quiet ai-usage.service; then
    echo "✓ 服务已启动：http://127.0.0.1:$PORT"
    echo "  查日志：journalctl --user -u ai-usage -f"
    echo "  停/禁用：systemctl --user disable --now ai-usage.service"
    echo
    # 装完立刻告诉用户三家分别通没通。诊断失败不等于安装失败，故 || true：
    # 没配 key、没配代理属于待办事项，不该让 install.sh 以非零退出。
    (cd "$PROJECT_DIR" && "$UV_BIN" run --no-sync python -m server.doctor) || true
else
    echo "✗ 服务启动失败，看日志：journalctl --user -u ai-usage -n 50 --no-pager" >&2
    exit 1
fi
