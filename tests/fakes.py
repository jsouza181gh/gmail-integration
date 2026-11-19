"""Scriptable fake of the googleapiclient resource tree used by the tests."""
from collections.abc import Callable
from typing import Protocol

from googleapiclient.errors import HttpError


class Handler(Protocol):
    def __call__(self, **kw: object) -> object: ...


class _Method(Protocol):
    def __call__(self, **kw: object) -> "_Req": ...


class _Req:
    def __init__(self, fn: Callable[[], object]) -> None:
        self.fn = fn

    def execute(self, num_retries: int = 0) -> object:
        return self.fn()


def http_error(status: int) -> HttpError:
    return HttpError(type("R", (), {"status": status, "reason": "x"})(), b"{}")


class _Group:
    def __init__(self, svc: "FakeService", prefix: str) -> None:
        self.svc, self.prefix = svc, prefix

    def __getattr__(self, method: str) -> _Method:
        def call(**kw: object) -> _Req:
            return self.svc.call(f"{self.prefix}.{method}", kw)

        return call


class FakeService:
    """handlers: {"watch": fn(**kw) -> dict, "history.list": ..., ...}.

    A handler may raise (e.g. http_error(404)). Every call is recorded in
    self.calls as (name, kwargs).
    """

    def __init__(self, **handlers: Handler) -> None:
        self.handlers = {k.replace("__", "."): v for k, v in handlers.items()}
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, name: str, kwargs: dict[str, object]) -> _Req:
        self.calls.append((name, kwargs))
        return _Req(lambda: self.handlers[name](**kwargs))

    def users(self) -> "_Users":
        return _Users(self, "users")


class _Users(_Group):
    def history(self) -> _Group:
        return _Group(self.svc, "history")

    def messages(self) -> _Group:
        return _Group(self.svc, "messages")

    def labels(self) -> _Group:
        return _Group(self.svc, "labels")

    def settings(self) -> "_Settings":
        return _Settings(self.svc, "settings")


class _Settings(_Group):
    def filters(self) -> _Group:
        return _Group(self.svc, "settings.filters")
