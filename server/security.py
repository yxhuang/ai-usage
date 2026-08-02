"""本地面板的写操作防护。

前提：`127.0.0.1:8788` 并不是只有本面板能访问——你打开的任何一个网页都能对本地端口
发请求。CORS 只挡住读响应，副作用照样会发生。所以凡是会改变状态的端点都要过这一关。

三道：

1. **Host 头**必须是本机回环地址，挡 DNS rebinding（把恶意域名解析到 127.0.0.1）。
2. **自定义头** `X-Requested-By`。这是主防线：跨域请求带非 safelisted 自定义头必然
   触发预检，而本服务不注册任何 CORS 中间件，预检必然失败，浏览器不会发出实际请求。
3. **Origin 头**必须存在且匹配。Fetch 规范要求非 GET/HEAD 请求携带 Origin，
   所以这里是 fail-closed：缺失也拒绝。命令行工具走 CLI，不经过 HTTP，不需要为它放宽。

另有点击劫持：上面三道都只防「恶意网页自己发请求」。攻击者还可以把本面板套进一个透明
iframe 诱导你点真开关——那时请求由面板自己发出，三道全部合法通过。所以还要给所有响应
下发 frame-ancestors，见 SecurityHeadersMiddleware。
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

REQUESTED_BY = "ai-usage-panel"
_LOOPBACK = ("127.0.0.1", "localhost", "[::1]")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        # 必须是 HTTP 响应头：<meta> 里的 CSP 不支持 frame-ancestors。
        # X-Frame-Options 是给不支持 CSP L2 的老浏览器兜底。
        resp.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        resp.headers["X-Frame-Options"] = "DENY"
        return resp


def _host_ok(host: str | None, port: int) -> bool:
    if not host or "," in host:  # 缺失或重复头一律拒绝
        return False
    return host.strip() in {f"{h}:{port}" for h in _LOOPBACK}


def require_local_ui(request: Request) -> None:
    """给会改状态的端点当依赖用。校验不过一律 403，不透露细节。"""
    port = request.url.port or 8788

    # 不信任 X-Forwarded-Host：它由中间层写入，攻击者可控
    if not _host_ok(request.headers.get("host"), port):
        raise HTTPException(status_code=403, detail="非法来源")

    if request.headers.get("x-requested-by") != REQUESTED_BY:
        raise HTTPException(status_code=403, detail="非法来源")

    origin = request.headers.get("origin")
    if not origin or origin == "null":
        raise HTTPException(status_code=403, detail="非法来源")
    if origin not in {f"http://{h}:{port}" for h in _LOOPBACK}:
        raise HTTPException(status_code=403, detail="非法来源")
