"""LLM calls on behalf of the current account, with that account's own keys.

`AccountLLM` (app or chat use) and `AccountBackfillLLM` look like a gateway to
the agent, indexing and classification code, but every call resolves the
account in scope -> its user's keys -> a gateway built for that key. Gateways
are cached per (key, use): pacing, cooldown and quota memory belong to the key,
so one user's quota trouble never slows another's, and two accounts that paste
the same key share its memory (as they share its quota).

No key means `NoLLMKeyError`: never another account's key and never a platform
key. One explicit exception keeps production working between the deploy and
the CLI that imports the `.env` keys: the admin account (the first admin) falls
back to the environment's keys, and the fallback is logged.
"""
import threading
import time
from collections.abc import Callable, Sequence
from typing import Literal, Protocol

from langchain_core.tools import BaseTool
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from src.db.accounts import admin_account_id
from src.db.scope import current_account
from src.llm.backfill import BackfillLLM
from src.llm.errors import NO_KEY_TEXT, LLMFatalError, NoLLMKeyError
from src.llm.keys import AccountKeys, keys_for_account, mark_key_error
from src.llm.types import (
    AssistantTurn,
    CallContext,
    Check,
    EmbedInput,
    EmbedResult,
    EmbedTask,
    Prompt,
    StructuredResult,
)
from src.utils.console import log

GatewayKind = Literal["app", "chat"]
# A pacing/rate wait longer than this goes back to the job queue instead of
# sleeping in the worker lane (shorter ones are slept inline).
INLINE_WAIT_MAX_S = 15.0
KEY_TTL_S = 30.0
GATEWAY_IDLE_S = 3600.0
GATEWAY_MAX = 256
REJECTED_TEXT = (
    "O Google recusou a chave do Gemini desta conta. Abra o painel, gere uma chave nova "
    "no Google AI Studio e cadastre de novo."
)
# What a gateway's fatal error says when the key itself is the problem.
KEY_REJECTED_MARKERS = (
    "API key not valid", "API_KEY_INVALID", "PERMISSION_DENIED", "UNAUTHENTICATED", "401", "403",
)


class ProviderGateway(Protocol):
    """What the per-key gateways offer (`LLMGateway` satisfies it)."""

    def under_pressure(self) -> bool: ...

    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T], check: Check[T] | None = None
    ) -> StructuredResult[T]: ...

    def generate_turn(self, prompt: Prompt, tools: Sequence[BaseTool]) -> AssistantTurn: ...

    def embed(
        self,
        inputs: Sequence[EmbedInput],
        *,
        purpose: str,
        task: EmbedTask,
        context: CallContext = CallContext(),
    ) -> EmbedResult: ...


type GatewayBuilder = Callable[[str, GatewayKind], ProviderGateway]


class GatewayPool:
    """key -> gateway, with idle expiry. One gateway per (key, use)."""

    def __init__(
        self, build: GatewayBuilder, *, clock: Callable[[], float] = time.monotonic,
        idle_s: float = GATEWAY_IDLE_S, max_entries: int = GATEWAY_MAX,
    ) -> None:
        self._build = build
        self._clock = clock
        self._idle_s = idle_s
        self._max = max_entries
        self._lock = threading.Lock()
        self._items: dict[tuple[str, GatewayKind], tuple[float, ProviderGateway]] = {}

    def gateway(self, key: str, kind: GatewayKind) -> ProviderGateway:
        now = self._clock()
        with self._lock:
            for k in [k for k, (seen, _) in self._items.items() if now - seen > self._idle_s]:
                del self._items[k]
            hit = self._items.get((key, kind))
            gw = hit[1] if hit is not None else self._build(key, kind)
            self._items[(key, kind)] = (now, gw)
            while len(self._items) > self._max:  # the longest idle goes
                del self._items[min(self._items, key=lambda k: self._items[k][0])]
            return gw


class _Entry:
    __slots__ = ("at", "keys", "rejected")

    def __init__(self, at: float, keys: AccountKeys | None, rejected: bool = False) -> None:
        self.at = at
        self.keys = keys
        self.rejected = rejected


class KeyResolver:
    """account -> keys, cached for `ttl_s` (a saved key reaches the worker within it)."""

    def __init__(
        self, sf: sessionmaker[Session], *,
        env_keys: Callable[[], AccountKeys | None] = lambda: None,
        clock: Callable[[], float] = time.monotonic, ttl_s: float = KEY_TTL_S,
    ) -> None:
        self.sf = sf
        self._env = env_keys
        self._clock = clock
        self._ttl = ttl_s
        self._lock = threading.Lock()
        self._cache: dict[int, _Entry] = {}
        self._announced: set[int] = set()

    def _load(self, account_id: int) -> AccountKeys | None:
        keys = keys_for_account(self.sf, account_id)
        env = self._env() if keys is None else None
        if keys is not None or env is None:
            return keys
        if admin_account_id(self.sf) != account_id:
            return None
        if account_id not in self._announced:
            self._announced.add(account_id)
            log(f"conta {account_id} (admin) sem chave cadastrada: usando as chaves do .env; "
                "rode `python -m src.cli.import_env_keys --account <email>`")
        return env

    def keys(self, account_id: int) -> AccountKeys:
        now = self._clock()
        with self._lock:
            hit = self._cache.get(account_id)
            if hit is None or now - hit.at >= self._ttl:
                hit = self._cache[account_id] = _Entry(now, self._load(account_id))
            if hit.keys is None:
                raise NoLLMKeyError(NO_KEY_TEXT)
            if hit.rejected:
                raise NoLLMKeyError(REJECTED_TEXT)
            return hit.keys

    def invalidate(self, account_id: int) -> None:
        with self._lock:
            self._cache.pop(account_id, None)

    def reject(self, account_id: int, error: str) -> None:
        """The provider refused this account's key: say so in the panel, and fail
        fast for a while instead of asking the provider again on every call."""
        mark_key_error(self.sf, account_id, REJECTED_TEXT)
        with self._lock:
            entry = self._cache.get(account_id)
            self._cache[account_id] = _Entry(self._clock(), entry.keys if entry else None, True)
        log(f"chave da conta {account_id} recusada pelo Google: {error[:120]}")


def _guarded[R](resolver: KeyResolver, account_id: int, call: Callable[[], R]) -> R:
    try:
        return call()
    except LLMFatalError as exc:
        if any(m in str(exc) for m in KEY_REJECTED_MARKERS):
            resolver.reject(account_id, str(exc))
            raise NoLLMKeyError(REJECTED_TEXT) from exc
        raise


class AccountLLM:
    """The app's (or the chat's) LLM: the current account's own key."""

    def __init__(self, resolver: KeyResolver, pool: GatewayPool, kind: GatewayKind) -> None:
        self._resolver = resolver
        self._pool = pool
        self._kind: GatewayKind = kind

    def _run[R](self, call: Callable[[ProviderGateway], R]) -> R:
        account = current_account()
        gw = self._pool.gateway(self._resolver.keys(account).app, self._kind)
        return _guarded(self._resolver, account, lambda: call(gw))

    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T], check: Check[T] | None = None
    ) -> StructuredResult[T]:
        return self._run(lambda gw: gw.generate_structured(prompt, schema, check))

    def generate_turn(self, prompt: Prompt, tools: Sequence[BaseTool]) -> AssistantTurn:
        return self._run(lambda gw: gw.generate_turn(prompt, tools))

    def embed(
        self,
        inputs: Sequence[EmbedInput],
        *,
        purpose: str,
        task: EmbedTask,
        context: CallContext = CallContext(),
    ) -> EmbedResult:
        return self._run(
            lambda gw: gw.embed(inputs, purpose=purpose, task=task, context=context)
        )


class AccountBackfillLLM:
    """The history backfill's LLM: the account's backfill key (or its app key), plus
    the app key in turbo — both keys belong to the account in scope."""

    def __init__(
        self, resolver: KeyResolver, pool: GatewayPool, turbo: Callable[[], bool]
    ) -> None:
        self._resolver = resolver
        self._pool = pool
        self._turbo = turbo
        self._lock = threading.Lock()
        self._routers: dict[tuple[str, str | None], BackfillLLM] = {}

    def _router(self, keys: AccountKeys) -> BackfillLLM:
        app = keys.app
        own = keys.backfill if keys.separate else None
        ident = (own or app, app if own else None)
        with self._lock:
            router = self._routers.get(ident)
            if router is None:
                if len(self._routers) >= GATEWAY_MAX:
                    self._routers.clear()
                if own is None:  # one key: the backfill shares the app gateway
                    router = BackfillLLM(self._pool.gateway(app, "app"), None, lambda: False)
                else:
                    router = BackfillLLM(
                        self._pool.gateway(own, "app"), self._pool.gateway(app, "app"),
                        self._turbo,
                    )
                self._routers[ident] = router
            return router

    def _run[R](self, call: Callable[[BackfillLLM], R]) -> R:
        account = current_account()
        router = self._router(self._resolver.keys(account))
        return _guarded(self._resolver, account, lambda: call(router))

    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T], check: Check[T] | None = None
    ) -> StructuredResult[T]:
        return self._run(lambda r: r.generate_structured(prompt, schema, check))

    def embed(
        self,
        inputs: Sequence[EmbedInput],
        *,
        purpose: str,
        task: EmbedTask,
        context: CallContext = CallContext(),
    ) -> EmbedResult:
        return self._run(lambda r: r.embed(inputs, purpose=purpose, task=task, context=context))


class AccountLLMs:
    """Everything the worker, API and CLIs need to call the LLM per account."""

    def __init__(
        self, sf: sessionmaker[Session], *, inline_wait_s: float | None = INLINE_WAIT_MAX_S,
        env_keys: Callable[[], AccountKeys | None] = lambda: None,
    ) -> None:
        from src.agent.settings import mode_source, turbo_source
        from src.llm.config import build_chat_gateway, build_gateway, load_llm_config
        from src.llm.recorder import DbCallRecorder

        mode = mode_source(sf, load_llm_config().mode)

        def build(key: str, kind: GatewayKind) -> ProviderGateway:
            recorder = DbCallRecorder(sf)
            if kind == "chat":
                return build_chat_gateway(recorder, mode, key=key, max_wait_s=inline_wait_s)
            return build_gateway(recorder, mode, key=key, max_wait_s=inline_wait_s)

        self.resolver = KeyResolver(sf, env_keys=env_keys)
        self.pool = GatewayPool(build)
        self.app = AccountLLM(self.resolver, self.pool, "app")
        self.chat = AccountLLM(self.resolver, self.pool, "chat")
        self.backfill = AccountBackfillLLM(self.resolver, self.pool, turbo_source(sf))
