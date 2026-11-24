"""LLM calls run with the current account's own keys, and only those."""
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from src.agent.settings import RuntimeSettings, mode_source, turbo_source
from src.db.models import User
from src.db.scope import ScopeError, account_scope, system_scope
from src.llm.accounts import (
    AccountBackfillLLM,
    AccountLLM,
    GatewayPool,
    KeyResolver,
    ProviderGateway,
)
from src.llm.errors import LLMFatalError, NoLLMKeyError
from src.llm.keys import AccountKeys, key_status, save_keys
from src.llm.types import (
    AssistantTurn,
    CallContext,
    Check,
    ChatMessage,
    EmbedInput,
    EmbedResult,
    EmbedTask,
    Prompt,
    StructuredResult,
    TokenUsage,
)

FERNET = "2xJ0yNw0b7a1Y8l8Cq0Rk3F2nQm8sV9pQ0rW4tZ6uX8="
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
P = Prompt(purpose="t", messages=(ChatMessage(role="human", content="oi"),))


class Out(BaseModel):
    x: int


class FakeGateway:
    """Answers with the key it was built for, so tests see which key served a call."""

    def __init__(self, key: str, kind: str) -> None:
        self.key, self.kind = key, kind
        self.pressure = False
        self.error: Exception | None = None
        self.calls = 0

    def under_pressure(self) -> bool:
        return self.pressure

    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T], check: Check[T] | None = None
    ) -> StructuredResult[T]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return StructuredResult[T](parsed=schema.model_validate({"x": 1}),
                                   model=f"{self.kind}:{self.key}", usage=TokenUsage())

    def generate_turn(self, prompt: Prompt, tools: Sequence[BaseTool]) -> AssistantTurn:
        self.calls += 1
        return AssistantTurn(content="ok", tool_calls=(), model=f"{self.kind}:{self.key}",
                             usage=TokenUsage())

    def embed(
        self, inputs: Sequence[EmbedInput], *, purpose: str, task: EmbedTask,
        context: CallContext = CallContext(),
    ) -> EmbedResult:
        self.calls += 1
        return EmbedResult(vectors=((1.0,),), model=f"{self.kind}:{self.key}")


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def build(key: str, kind: str) -> ProviderGateway:
    return FakeGateway(key, kind)


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET", FERNET)


@pytest.fixture
def sf2(session_factory: sessionmaker[Session], second_account: int) -> sessionmaker[Session]:
    with system_scope(), session_factory.begin() as s:
        for uid in (1, 2):
            s.add(User(id=uid, username=f"u{uid}", email=f"u{uid}@x.org", password_hash="x",
                       totp_secret_enc="x", created_at=NOW, password_changed_at=NOW,
                       role="admin" if uid == 1 else "member"))
    return session_factory


def make(
    sf: sessionmaker[Session], *, env: AccountKeys | None = None, clock: Clock | None = None
) -> tuple[KeyResolver, GatewayPool]:
    clock = clock or Clock()
    return KeyResolver(sf, env_keys=lambda: env, clock=clock, ttl_s=30), GatewayPool(build, clock=clock)


def test_each_account_is_served_with_its_own_key(sf2: sessionmaker[Session]) -> None:
    save_keys(sf2, 1, app_key="KA", backfill_key=None, now=NOW)
    save_keys(sf2, 2, app_key="KB", backfill_key=None, now=NOW)
    resolver, pool = make(sf2)
    llm = AccountLLM(resolver, pool, "app")
    assert llm.generate_structured(P, Out).model == "app:KA"
    with account_scope(2):
        assert llm.generate_structured(P, Out).model == "app:KB"
        assert llm.embed([EmbedInput(text="a")], purpose="e", task="RETRIEVAL_QUERY").model == "app:KB"


def test_a_keyless_account_never_borrows_another_key(sf2: sessionmaker[Session]) -> None:
    save_keys(sf2, 1, app_key="KA", backfill_key=None, now=NOW)
    resolver, pool = make(sf2, env=AccountKeys(app="ENV"))
    llm = AccountLLM(resolver, pool, "app")
    with account_scope(2), pytest.raises(NoLLMKeyError):
        llm.generate_structured(P, Out)  # member: no row, and the env key is admin-only


def test_no_account_in_scope_is_an_error(sf2: sessionmaker[Session]) -> None:
    resolver, pool = make(sf2)
    with system_scope(), pytest.raises(ScopeError):
        AccountLLM(resolver, pool, "app").generate_structured(P, Out)


def test_admin_account_falls_back_to_env_keys_until_imported(
    sf2: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    resolver, pool = make(sf2, env=AccountKeys(app="ENV", backfill="ENVBF"))
    llm = AccountLLM(resolver, pool, "app")
    assert llm.generate_structured(P, Out).model == "app:ENV"
    llm.generate_structured(P, Out)
    assert capsys.readouterr().out.count("chaves do .env") == 1  # logged, once
    save_keys(sf2, 1, app_key="KA", backfill_key=None, now=NOW)  # CLI ran
    resolver.invalidate(1)
    assert llm.generate_structured(P, Out).model == "app:KA"


def test_key_change_is_seen_after_the_ttl(sf2: sessionmaker[Session]) -> None:
    clock = Clock()
    save_keys(sf2, 1, app_key="K1", backfill_key=None, now=NOW)
    resolver, pool = make(sf2, clock=clock)
    llm = AccountLLM(resolver, pool, "app")
    assert llm.generate_structured(P, Out).model == "app:K1"
    save_keys(sf2, 1, app_key="K2", backfill_key=None, now=NOW)
    assert llm.generate_structured(P, Out).model == "app:K1"
    clock.t += 31
    assert llm.generate_structured(P, Out).model == "app:K2"


def test_gateway_is_cached_per_key_and_kind(sf2: sessionmaker[Session]) -> None:
    resolver, pool = make(sf2)
    assert pool.gateway("K", "app") is pool.gateway("K", "app")
    assert pool.gateway("K", "app") is not pool.gateway("K", "chat")
    assert pool.gateway("K", "app") is not pool.gateway("K2", "app")


def test_idle_gateways_expire(sf2: sessionmaker[Session]) -> None:
    clock = Clock()
    pool = GatewayPool(build, clock=clock, idle_s=100)
    first = pool.gateway("K", "app")
    clock.t += 50
    assert pool.gateway("K", "app") is first  # used again: stays
    clock.t += 101
    assert pool.gateway("K", "app") is not first


def test_two_accounts_with_the_same_key_share_quota_memory(sf2: sessionmaker[Session]) -> None:
    save_keys(sf2, 1, app_key="SAME", backfill_key=None, now=NOW)
    save_keys(sf2, 2, app_key="SAME", backfill_key=None, now=NOW)
    resolver, pool = make(sf2)
    assert pool.gateway("SAME", "app") is pool.gateway("SAME", "app")


def test_a_rejected_key_is_recorded_and_explained(sf2: sessionmaker[Session]) -> None:
    save_keys(sf2, 1, app_key="BAD", backfill_key=None, now=NOW)
    resolver, pool = make(sf2)
    pool.gateway("BAD", "app").error = LLMFatalError("erro não recuperável: API key not valid")  # type: ignore[attr-defined]
    llm = AccountLLM(resolver, pool, "app")
    with pytest.raises(NoLLMKeyError):
        llm.generate_structured(P, Out)
    assert key_status(sf2, 1).last_error is not None


def test_other_fatal_errors_pass_through(sf2: sessionmaker[Session]) -> None:
    save_keys(sf2, 1, app_key="K", backfill_key=None, now=NOW)
    resolver, pool = make(sf2)
    pool.gateway("K", "app").error = LLMFatalError("400 bad request")  # type: ignore[attr-defined]
    with pytest.raises(LLMFatalError):
        AccountLLM(resolver, pool, "app").generate_structured(P, Out)
    assert key_status(sf2, 1).last_error is None


def test_backfill_uses_its_own_key_or_the_app_key(sf2: sessionmaker[Session]) -> None:
    save_keys(sf2, 1, app_key="KA", backfill_key="KBF", now=NOW)
    save_keys(sf2, 2, app_key="KB", backfill_key=None, now=NOW)
    resolver, pool = make(sf2)
    llm = AccountBackfillLLM(resolver, pool, lambda: False)
    assert llm.generate_structured(P, Out).model == "app:KBF"
    with account_scope(2):
        assert llm.generate_structured(P, Out).model == "app:KB"


def test_turbo_alternates_between_the_accounts_own_keys_only(sf2: sessionmaker[Session]) -> None:
    save_keys(sf2, 1, app_key="KA", backfill_key="KBF", now=NOW)
    save_keys(sf2, 2, app_key="KB", backfill_key="KBF2", now=NOW)
    resolver, pool = make(sf2)
    llm = AccountBackfillLLM(resolver, pool, lambda: True)
    seen = [llm.generate_structured(P, Out).model for _ in range(3)]
    assert seen == ["app:KBF", "app:KA", "app:KBF"]
    with account_scope(2):
        assert [llm.generate_structured(P, Out).model for _ in range(2)] == ["app:KBF2", "app:KB"]


def test_turbo_and_mode_are_remembered_per_account(sf2: sessionmaker[Session]) -> None:
    RuntimeSettings(sf2).set_backfill_turbo(True)
    RuntimeSettings(sf2).set_llm_mode("fast")
    turbo, mode = turbo_source(sf2), mode_source(sf2, "free")
    assert turbo() is True and mode() == "fast"
    with account_scope(2):
        assert turbo() is False and mode() == "free"


def test_key_for_resolver_without_rows_and_env(sf2: sessionmaker[Session]) -> None:
    resolver, _ = make(sf2)
    with pytest.raises(NoLLMKeyError):
        resolver.keys(1)
