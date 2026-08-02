"""支持显式配置路径的服务启动入口。"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from .config import resolve_config
from .main import create_app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 AI Usage Panel 服务")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="显式指定 config.toml（相对路径基于当前工作目录）",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        cfg, source = resolve_config(args.config)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.exit(2, f"{parser.prog}: 配置错误: {exc}\n")

    uvicorn.run(
        create_app(cfg, source), host=cfg.server.host, port=cfg.server.port
    )


if __name__ == "__main__":
    main(sys.argv[1:])
