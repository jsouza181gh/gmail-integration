from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.approvals import ApprovalService
from src.agent.rules import RuleAdmin
from src.cli import actions as actions_cli
from src.cli import rules as rules_cli
from src.db.models import AutomationRule, Email, SuggestedAction
from src.jobs.store import JobStore


@pytest.fixture
def svc(session_factory: sessionmaker[Session]) -> ApprovalService:
    now = datetime.now(UTC)
    with session_factory.begin() as s:
        for eid, sender in (("a", "Loja <ofertas@loja.com>"), ("b", "Loja <ofertas@loja.com>"),
                            ("c", "Ana <ana@x.com>")):
            s.add(Email(id=eid, sender=sender, subject=f"assunto {eid}", attempts=0))
            s.add(SuggestedAction(email_id=eid, kind="archive", reason="promo", status="pending",
                                  origin="agent", created_at=now))
    return ApprovalService(session_factory, JobStore(session_factory))


class Out:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)


def _statuses(svc: ApprovalService) -> dict[str, str]:
    with svc.sf() as s:
        return {a.email_id: a.status for a in s.scalars(select(SuggestedAction))}


def test_cli_list_shows_pending(svc: ApprovalService) -> None:
    out = Out()
    assert actions_cli.main(["list", "--sender", "loja.com"], service=svc, out=out) == 0
    text = "\n".join(out.lines)
    assert "assunto a" in text and "assunto b" in text and "assunto c" not in text


def test_cli_approve_by_sender_batches(svc: ApprovalService) -> None:
    out = Out()
    code = actions_cli.main(["approve", "--sender", "ofertas@loja.com", "--yes"], service=svc, out=out)
    assert code == 0
    assert _statuses(svc) == {"a": "approved", "b": "approved", "c": "pending"}
    assert any("2" in line for line in out.lines)


def test_cli_batch_asks_confirmation(svc: ApprovalService) -> None:
    out = Out()
    code = actions_cli.main(
        ["approve", "--sender", "loja.com"], service=svc, out=out, ask=lambda prompt: "n"
    )
    assert code == 1 and _statuses(svc)["a"] == "pending"


def test_cli_reject_with_comment(svc: ApprovalService) -> None:
    out = Out()
    code = actions_cli.main(
        ["reject", "--id", "3", "--comment", "Ana é minha amiga", "--yes"], service=svc, out=out
    )
    assert code == 0 and _statuses(svc)["c"] == "rejected"


def test_cli_requires_a_selector(svc: ApprovalService) -> None:
    with pytest.raises(SystemExit):
        actions_cli.main(["approve", "--yes"], service=svc, out=Out())


# --- rules CLI --------------------------------------------------------------------

def test_rules_add_list_promote_disable(session_factory: sessionmaker[Session]) -> None:
    admin = RuleAdmin(session_factory)
    out = Out()
    assert rules_cli.main(
        ["add", "--name", "lojas", "--domain", "loja.com", "--action", "trash"], admin=admin, out=out
    ) == 0
    with session_factory() as s:
        rule = s.scalars(select(AutomationRule)).one()
        assert (rule.mode, rule.sender_domain, rule.action) == ("suggest", "loja.com", "trash")
    rules_cli.main(["list"], admin=admin, out=out)
    assert any("lojas" in line for line in out.lines)
    assert rules_cli.main(["promote", "--id", str(rule.id), "--yes"], admin=admin, out=out) == 0
    rules_cli.main(["disable", "--id", str(rule.id)], admin=admin, out=out)
    with session_factory() as s:
        rule = s.scalars(select(AutomationRule)).one()
        assert (rule.mode, rule.active) == ("auto", False)


def test_promote_refuses_block_sender(session_factory: sessionmaker[Session]) -> None:
    admin = RuleAdmin(session_factory)
    rid = admin.add(name="spam", sender_address="x@spam.com", action="block_sender")
    out = Out()
    assert rules_cli.main(["promote", "--id", str(rid), "--yes"], admin=admin, out=out) == 1
    with session_factory() as s:
        assert s.get_one(AutomationRule, rid).mode == "suggest"


def test_rules_add_requires_condition(session_factory: sessionmaker[Session]) -> None:
    out = Out()
    code = rules_cli.main(
        ["add", "--name", "x", "--action", "trash"], admin=RuleAdmin(session_factory), out=out
    )
    assert code == 1
