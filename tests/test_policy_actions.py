from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.actions import ActionService
from src.agent.executor import ToolExecutor
from src.agent.policy import PolicyResolver
from src.agent.sender_history import DbSenderHistory
from src.agent.state import ActionProposed, AgentState, EmailFacts
from src.agent.tools import build_registry
from src.agent.trail import Trail
from src.agent.types import ActionKind, PolicyDecision, RuleMode, RuleRef
from src.db.models import AgentConfig, Email, Job, SuggestedAction, ToolPolicy
from src.jobs.store import JobStore
from src.jobs.types import ExecuteActionJob
from src.llm.types import ToolCallRequest
from tests.agent_fakes import make_ctx, make_email


@pytest.fixture
def sf(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    with session_factory.begin() as s:
        s.add(AgentConfig(id=1, version=1, active=True, decide_prompt="p", max_turns=3))
        s.add(Email(id="a", sender="Loja <ofertas@loja.com>", attempts=0, category="Promoções"))
    return session_factory


def _policy(sf: sessionmaker[Session], target: str, policy: str, trigger: str = "webhook") -> None:
    with sf.begin() as s:
        s.add(ToolPolicy(target=target, trigger=trigger, policy=policy))


def _service(sf: sessionmaker[Session]) -> ActionService:
    return ActionService(sf, JobStore(sf), PolicyResolver(sf))


def _rule(mode: RuleMode) -> RuleRef:
    return RuleRef(rule_id=1, name="lojas", mode=mode)


# --- policy ---------------------------------------------------------------------

def test_missing_policy_defaults_to_suggest(sf: sessionmaker[Session]) -> None:
    assert PolicyResolver(sf).resolve("archive", "webhook") == "suggest"


@pytest.mark.parametrize(
    ("db", "rule_mode"), [("auto", None), ("suggest", "auto"), ("auto", "auto")]
)
def test_block_sender_never_auto(
    sf: sessionmaker[Session], db: PolicyDecision, rule_mode: RuleMode | None
) -> None:
    _policy(sf, "block_sender", db)
    assert PolicyResolver(sf).resolve("block_sender", "webhook", rule_mode=rule_mode) == "suggest"


def test_db_deny_beats_rule_auto(sf: sessionmaker[Session]) -> None:
    _policy(sf, "trash", "deny")
    assert PolicyResolver(sf).resolve("trash", "webhook", rule_mode="auto") == "deny"


def test_rule_auto_applies_when_db_allows(sf: sessionmaker[Session]) -> None:
    assert PolicyResolver(sf).resolve("trash", "webhook", rule_mode="auto") == "auto"


def test_policy_is_per_trigger(sf: sessionmaker[Session]) -> None:
    _policy(sf, "archive", "deny", trigger="chat")
    resolver = PolicyResolver(sf)
    assert (resolver.resolve("archive", "chat"), resolver.resolve("archive", "webhook")) == (
        "deny", "suggest"
    )


# --- action service ---------------------------------------------------------------

def test_suggest_creates_pending_row_without_job(sf: sessionmaker[Session]) -> None:
    ref = _service(sf).propose("a", "archive", "promo", run_id=None, trigger="webhook")
    assert ref.outcome == "suggested" and ref.action_id is not None
    with sf() as s:
        row = s.get_one(SuggestedAction, ref.action_id)
        assert (row.status, row.origin, row.reason) == ("pending", "agent", "promo")
        assert s.scalars(select(Job)).all() == []


def test_auto_action_is_approved_and_enqueued(sf: sessionmaker[Session]) -> None:
    ref = _service(sf).propose(
        "a", "trash", "regra", run_id=None, trigger="webhook", rule=_rule("auto")
    )
    assert ref.outcome == "auto" and ref.action_id is not None
    with sf() as s:
        row = s.get_one(SuggestedAction, ref.action_id)
        assert (row.status, row.origin, row.rule_id) == ("approved", "rule", 1)
        assert row.decided_at is not None
        (job,) = s.scalars(select(Job)).all()
        assert (job.kind, job.action_id) == ("execute_action", ref.action_id)


def test_denied_writes_nothing(sf: sessionmaker[Session]) -> None:
    _policy(sf, "star", "deny")
    ref = _service(sf).propose("a", "star", "x", run_id=None, trigger="webhook")
    assert (ref.outcome, ref.action_id) == ("denied", None)
    with sf() as s:
        assert s.scalars(select(SuggestedAction)).all() == []


def test_duplicate_pending_proposal_is_not_created(sf: sessionmaker[Session]) -> None:
    svc = _service(sf)
    first = svc.propose("a", "archive", "1", run_id=None, trigger="webhook")
    second = svc.propose("a", "archive", "2", run_id=None, trigger="webhook")
    assert second.outcome == "duplicate" and second.action_id == first.action_id
    with sf() as s:
        assert len(s.scalars(select(SuggestedAction)).all()) == 1


def test_rejected_proposal_can_be_suggested_again(sf: sessionmaker[Session]) -> None:
    svc = _service(sf)
    first = svc.propose("a", "archive", "1", run_id=None, trigger="webhook")
    with sf.begin() as s:
        s.get_one(SuggestedAction, first.action_id).status = "rejected"
    assert svc.propose("a", "archive", "2", run_id=None, trigger="webhook").outcome == "suggested"


def test_partial_unique_index_guards_duplicates(sf: sessionmaker[Session]) -> None:
    from sqlalchemy.exc import IntegrityError

    now = datetime.now(UTC)
    with pytest.raises(IntegrityError), sf.begin() as s:
        for _ in range(2):
            s.add(SuggestedAction(email_id="a", kind="trash", reason="r", status="pending",
                                  origin="agent", created_at=now))
            s.flush()


# --- propose_action tool ------------------------------------------------------------

def _propose(sf: sessionmaker[Session], kind: ActionKind) -> tuple[str, object]:
    trail = Trail(sf)
    run = trail.start_run("webhook", "a", 1)
    ctx = make_ctx(run_id=run, actions=_service(sf), policy=PolicyResolver(sf))
    state = AgentState(emails={"a": EmailFacts(email=make_email("a"))})
    call = ToolCallRequest(id="c1", name="propose_action", args={"kind": kind, "reason": "promo"})
    res = ToolExecutor(build_registry(), trail).execute(call, ctx, state, source="model")
    assert res.ok, res.message.content
    return res.message.content, res.update


def test_propose_action_tool_reports_outcome_to_model(sf: sessionmaker[Session]) -> None:
    content, update = _propose(sf, "archive")
    assert "sugerido" in content and "aprovação" in content
    assert isinstance(update, ActionProposed) and update.ref.outcome == "suggested"
    with sf() as s:
        (row,) = s.scalars(select(SuggestedAction)).all()
        assert row.run_id is not None
    content, _ = _propose(sf, "archive")
    assert "já sugerido" in content


def test_propose_action_rejects_unknown_kind(sf: sessionmaker[Session]) -> None:
    trail = Trail(sf)
    run = trail.start_run("webhook", "a", 1)
    ctx = make_ctx(run_id=run, actions=_service(sf))
    state = AgentState(emails={"a": EmailFacts(email=make_email("a"))})
    call = ToolCallRequest(id="c1", name="propose_action", args={"kind": "delete", "reason": "x"})
    res = ToolExecutor(build_registry(), trail).execute(call, ctx, state, source="model")
    assert not res.ok and "argumentos inválidos" in res.message.content


def test_propose_action_offered_in_webhook() -> None:
    assert "propose_action" in {t.name for t in build_registry().for_model("webhook")}


# --- sender history learns from outcomes -----------------------------------------------

def test_sender_history_counts_past_outcomes(sf: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    with sf.begin() as s:
        s.add(Email(id="b", sender="Loja <ofertas@loja.com>", attempts=0))
        s.add_all([
            SuggestedAction(email_id="a", kind="archive", reason="r", status="rejected",
                            origin="agent", created_at=now),
            SuggestedAction(email_id="b", kind="archive", reason="r", status="rejected",
                            origin="agent", created_at=now),
            SuggestedAction(email_id="b", kind="trash", reason="r", status="executed",
                            origin="agent", created_at=now),
        ])
    stats = DbSenderHistory(sf).stats("ofertas@loja.com")
    assert {(a.kind, a.status, a.count) for a in stats.actions} == {
        ("archive", "rejected", 2), ("trash", "executed", 1)
    }
