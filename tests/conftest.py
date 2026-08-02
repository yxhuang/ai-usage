"""测试全局防护：把真实凭证类环境变量从测试进程里剥掉。

背景（真实事故，2026-07-26）：一条测试没有清理 KIMI_API_KEY，而开发机的
交互 shell 里 export 了真实 key，provider 走了"环境变量优先"分支，
断言失败时 pytest 把**真实 key 打进了失败输出**。

测试永远不该看见真实凭证——这里用 autouse fixture 从源头堵死，
而不是依赖每个测试作者记得自己 delenv。
"""

from __future__ import annotations

import pytest

# 任何可能承载真实凭证的环境变量都在这里列出
CREDENTIAL_ENV_VARS = (
    "KIMI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AI_USAGE_CONFIG",
)

# 开发机/CI 上的代理变量会污染"直连"语义的测试，一律剥掉
PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


@pytest.fixture(autouse=True)
def _isolate_credential_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CREDENTIAL_ENV_VARS + PROXY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
