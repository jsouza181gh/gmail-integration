"""HTTP hardening: real client IP, security headers, rate limit, CSRF/Origin.

All of it runs in one middleware so the order is explicit:
IP -> rate limit -> CSRF/Origin (writes under /api) -> route -> headers.
"""
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from ipaddress import IPv4Network, IPv6Network, ip_address

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from src.api.deps import CSRF_COOKIE, CSRF_HEADER, get_deps

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
NO_SESSION_WRITES = frozenset({  # Origin only: there is no session yet
    "/api/auth/login", "/api/auth/2fa",
    "/api/entry/invite", "/api/entry/signup", "/api/entry/login",
})
AUTH_PREFIX = "/api/auth/"
DEFAULT_LIMIT = 60
AUTH_LIMIT = 10
WINDOW_S = 60.0

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": (
        "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

Network = IPv4Network | IPv6Network


def client_ip(peer: str, xff: str | None, trusted: tuple[Network, ...]) -> str:
    """The peer, unless it is our own proxy: then the address it appended."""
    try:
        peer_addr = ip_address(peer)
    except ValueError:
        return peer
    if not xff or not any(peer_addr in net for net in trusted):
        return peer
    last = xff.split(",")[-1].strip()
    try:
        return str(ip_address(last))
    except ValueError:
        return peer


class RateLimiter:
    """Sliding window per key, in memory (the API runs one worker)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._clock = clock

    def hit(self, key: str, limit: int) -> float | None:
        """None when allowed; else seconds until a slot frees."""
        now = self._clock()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= WINDOW_S:
            q.popleft()
        if len(q) >= limit:
            return WINDOW_S - (now - q[0])
        q.append(now)
        if len(self._hits) > 10_000:  # forget idle keys
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < WINDOW_S}
        return None


def route_key(path: str) -> str:
    return re.sub(r"/[0-9a-fA-F-]{8,}|/\d+", "/{id}", path)


def _deny(status: int, detail: str) -> Response:
    return JSONResponse({"detail": detail}, status_code=status)


def security_middleware(
    limiter: RateLimiter,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        deps = getattr(request.app.state, "deps", None)
        path = request.url.path
        peer = request.client.host if request.client else "unknown"
        if deps is not None:
            request.state.ip = client_ip(
                peer, request.headers.get("x-forwarded-for"), get_deps(request).settings.trusted_proxies
            )
        ip = getattr(request.state, "ip", peer)

        response: Response
        if deps is not None and path.startswith("/api/"):
            limit = AUTH_LIMIT if path.startswith(AUTH_PREFIX) else DEFAULT_LIMIT
            wait = limiter.hit(f"{ip}|{request.method}|{route_key(path)}", limit)
            if wait is not None:
                response = _deny(429, "muitas requisições")
                response.headers["Retry-After"] = str(int(wait) + 1)
                return _with_headers(response, path)
            if request.method in WRITE_METHODS:
                problem = _csrf_problem(request, path)
                if problem is not None:
                    get_deps(request).log.record("csrf_invalid", ip, f"{request.method} {path}: {problem}")
                    return _with_headers(_deny(403, "requisição recusada"), path)
        response = await call_next(request)
        return _with_headers(response, path)

    return middleware


def _csrf_problem(request: Request, path: str) -> str | None:
    deps = get_deps(request)
    if request.headers.get("origin") != deps.settings.origin:
        return "origin"
    if path in NO_SESSION_WRITES:
        return None
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or cookie != header:
        return "csrf"
    return None


def _with_headers(response: Response, path: str) -> Response:
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response
