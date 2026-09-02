"""Host 与 Origin 本地访问边界（加固计划 Task 2）。

应用只绑定 127.0.0.1（REQ-002），但浏览器发起的跨站请求（CSRF）与
DNS rebinding 域名仍可能到达回环端口：本中间件按两层校验拒绝——

1. Host 头必须是回环主机（127.0.0.1/localhost/::1）或测试环境显式
   使用的 ``testserver``（无点号主机名无法被公共 DNS 解析，无 rebinding 面）；
2. 写方法（POST/PUT/PATCH/DELETE）携带 Origin 时必须是本机同源（任意
   端口）或显式开发来源；``Origin: null`` 与未知来源拒绝。

GET 与未携带 Origin 的请求（本地 CLI/curl/TestClient）保持原有成功语义；
拒绝响应统一 403 + 稳定错误信封，消息不含被拒 Host/Origin 的内容。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})


@dataclass(frozen=True)
class LocalAccessSettings:
    """本机访问边界配置；testserver 仅用于进程内 TestClient。"""

    allowed_hosts: tuple[str, ...] = field(default_factory=lambda: tuple(sorted(_LOOPBACK_HOSTS)))
    development_origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://localhost:5173")
    access_mode: Literal["enforced", "disabled"] = "enforced"


def _host_header_value(request: Request) -> str:
    host = request.headers.get("host") or ""
    host = host.strip().lower()
    if host.startswith("["):  # IPv6 字面量：取 ] 之前的部分
        return host[: host.find("]") + 1]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _host_is_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    return host in allowed_hosts


def _origin_is_allowed(origin: str, allowed_hosts: tuple[str, ...], development_origins: tuple[str, ...]) -> bool:
    value = origin.strip()
    if value.lower() == "null":
        return False
    if value in development_origins:
        return True
    from urllib.parse import urlsplit

    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        return False
    hostname = (parts.hostname or "").lower()
    return hostname in allowed_hosts or hostname in _LOOPBACK_HOSTS


def install_local_access_middleware(app: FastAPI, settings: LocalAccessSettings) -> None:
    if settings.access_mode == "disabled":
        return

    @app.middleware("http")
    async def local_access_boundary(request: Request, call_next):
        method = request.method.upper()
        host = _host_header_value(request)
        if not _host_is_allowed(host, settings.allowed_hosts):
            return JSONResponse(
                status_code=403,
                content={"detail": {"code": "untrusted_host", "message": "请求的来源主机不受信任，仅允许本机访问"}},
            )
        origin = request.headers.get("origin")
        if origin and method in _STATE_CHANGING_METHODS:
            if not _origin_is_allowed(origin, settings.allowed_hosts, settings.development_origins):
                return JSONResponse(
                    status_code=403,
                    content={"detail": {"code": "untrusted_origin", "message": "请求来源不被允许，请从本机界面使用"}},
                )
        return await call_next(request)
