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
    providers: list[Provider] = []
    for pid in PROVIDER_ORDER:
        pconf = cfg.providers.get(pid)
        if not pconf or not pconf.enabled:
            continue
        if pid == "claude":
            providers.append(
                ClaudeProvider(
                    credentials_path=pconf.options.get(
                        "credentials_path", "~/.claude/.credentials.json"
                    ),
                    proxy=pconf.options.get("proxy"),
                )
            )
        elif pid == "codex":
            providers.append(
                CodexProvider(
                    command=pconf.options.get("command", "codex-nowin"),
                    proxy=pconf.options.get("proxy", "http://127.0.0.1:7890"),
                    sessions_dir=pconf.options.get(
                        "sessions_dir", "~/.codex/sessions"
                    ),
                    app_server_timeout_seconds=pconf.options.get(
                        "app_server_timeout_seconds", 30
                    ),
                    app_server_max_failures=pconf.options.get(
                        "app_server_max_failures", 3
                    ),
                    app_server_retry_after_seconds=pconf.options.get(
                        "app_server_retry_after_seconds", 1800
                    ),
                )
            )
        elif pid == "kimi":
            providers.append(
                KimiProvider(
                    api_key=pconf.options.get("api_key"),
                    api_key_env=pconf.options.get("api_key_env", "KIMI_API_KEY"),
                    api_key_file=pconf.options.get(
                        "api_key_file", "~/.config/shell/secrets.sh"
                    ),
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

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app


app = create_app()


def main() -> None:
    cfg = load_config()
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port)


if __name__ == "__main__":
    main()
