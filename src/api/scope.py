"""Panel requests run in the logged-in user's account scope.

Pure ASGI (not BaseHTTPMiddleware): the ContextVar set here is what the
route, its thread-pool work and a streamed response all see.
"""
from collections.abc import Callable

import anyio
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from src.api.deps import ACCESS_COOKIE
from src.db.scope import account_scope

type ResolveAccount = Callable[[str], int | None]


class AccountScopeMiddleware:
    def __init__(self, app: ASGIApp, resolve: ResolveAccount) -> None:
        self.app = app
        self.resolve = resolve

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = (
            HTTPConnection(scope).cookies.get(ACCESS_COOKIE) if scope["type"] == "http" else None
        )
        account = await anyio.to_thread.run_sync(self.resolve, token) if token else None
        if account is None:  # protected routes answer 401 on their own
            await self.app(scope, receive, send)
            return
        with account_scope(account):
            await self.app(scope, receive, send)
