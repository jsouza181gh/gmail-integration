"""Scriptable fake of the googleapiclient resource tree used by the tests."""
from googleapiclient.errors import HttpError


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, num_retries=0):
        return self.fn()


def http_error(status):
    return HttpError(type("R", (), {"status": status, "reason": "x"})(), b"{}")


class _Group:
    def __init__(self, svc, prefix):
        self.svc, self.prefix = svc, prefix

    def __getattr__(self, method):
        return lambda **kw: self.svc.call(f"{self.prefix}.{method}", kw)


class FakeService:
    """handlers: {"watch": fn(**kw) -> dict, "history.list": ..., ...}.

    A handler may raise (e.g. http_error(404)). Every call is recorded in
    self.calls as (name, kwargs).
    """

    def __init__(self, **handlers):
        self.handlers = {k.replace("__", "."): v for k, v in handlers.items()}
        self.calls = []

    def call(self, name, kwargs):
        self.calls.append((name, kwargs))
        return _Req(lambda: self.handlers[name](**kwargs))

    def users(self):
        return _Users(self, "users")


class _Users(_Group):
    def history(self):
        return _Group(self.svc, "history")

    def messages(self):
        return _Group(self.svc, "messages")
