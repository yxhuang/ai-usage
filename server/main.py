"""FastAPI 入口：面板页面 + /api/summary + /api/refresh。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .cache import Cache
from .config import Config, load_config
from .poller import Poller
from .providers.base import Provider
from .providers.claude import ClaudeProvider
from .providers.codex import CodexProvider
from .providers.kimi import KimiProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = REPO_ROOT / "web"

# providers 固定顺序
PROVIDER_ORDER = ["claude", "codex", "kimi"]


def build_providers(cfg: Config) -> list[Provider]:
    # 默认值一律以 Config 为唯一来源，这里不再维护第二套 fallback
    providers: list[Provider] = []
    for pid in PROVIDER_ORDER:
        pconf = cfg.providers.get(pid)
        if not pconf or not pconf.enabled:
            continue
        if pid == "claude":
            providers.append(
                ClaudeProvider(
                    credentials_path=pconf.options["credentials_path"],
                    proxy=pconf.options.get("proxy"),
                )
            )
        elif pid == "codex":
            providers.append(
                CodexProvider(
                    command=pconf.options["command"],
                    proxy=pconf.options.get("proxy"),
                    sessions_dir=pconf.options["sessions_dir"],
                    app_server_timeout_seconds=pconf.options[
                        "app_server_timeout_seconds"
                    ],
                    app_server_max_failures=pconf.options["app_server_max_failures"],
                    app_server_retry_after_seconds=pconf.options[
                        "app_server_retry_after_seconds"
                    ],
                )
            )
        elif pid == "kimi":
            providers.append(
                KimiProvider(
                    api_key=pconf.options.get("api_key"),
                    api_key_env=pconf.options["api_key_env"],
                    api_key_file=pconf.options["api_key_file"],
                    proxy=pconf.options.get("proxy"),
                )
            )
    return providers


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    cache = Cache()
    providers = build_providers(cfg)
    poller = Poller(providers, cfg.poller, cache)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cache.load()
        task = asyncio.create_task(poller.run_forever())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(lifespan=lifespan)

    def summary_payload() -> dict:
        stale_after = cfg.poller.stale_after_seconds
        usages = cache.mark_stale_if_old(stale_after)
        by_id = {u.id: u for u in usages}
        ordered = [by_id[pid] for pid in PROVIDER_ORDER if pid in by_id]
        updated = max((u.fetched_at for u in ordered), default=None)
        return {
            "updated_at": updated.isoformat() if updated else None,
            "providers": [u.to_dict() for u in ordered],
        }

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/summary")
    async def get_summary():
        return summary_payload()

    @app.post("/api/refresh")
    async def post_refresh(provider: str = Query("all")):
        if provider != "all":
            known = {p.id for p in providers}
            if provider not in known:
                raise HTTPException(status_code=400, detail=f"未知 provider: {provider}")
            await poller.refresh(provider)
        else:
            await poller.refresh(None)
        return summary_payload()

    # 面板是常驻窗口，改完样式要能立刻看见。Chrome 的 --app 窗口会直接吃内存缓存、
    # 普通刷新都不回源，所以显式要求每次都revalidate——本来就是回环地址，不费什么。
    class NoCacheStatic(StaticFiles):
        def file_response(self, *args, **kwargs):
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "no-cache"
            return resp

    app.mount("/static", NoCacheStatic(directory=WEB_DIR), name="static")
    return app


app = create_app()


def main() -> None:
    cfg = load_config()
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port)


if __name__ == "__main__":
    main()
