from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.agent.rules import RuleEngine
from src.classification.taxonomy import Classification
from src.db.models import AutomationRule
from tests.agent_fakes import make_email

CLS = Classification(
    category="Promoções",
    reason="r",
    is_relevant=False,
    deadline=None,
    has_document=False,
    ephemeral=True,
    category_set_id=1,
)


def _add(sf: sessionmaker[Session], **fields: object) -> int:
    base: dict[str, object] = {
        "name": "r", "action": "archive", "mode": "suggest", "active": True,
        "fired_count": 0, "approved_count": 0, "rejected_count": 0, "source": "cli",
        "created_at": datetime.now(UTC),
    }
    with sf.begin() as s:
        rule = AutomationRule(**{**base, **fields})
        s.add(rule)
        s.flush()
        return rule.id


def test_rule_matches_domain_and_category_case_insensitive(
    session_factory: sessionmaker[Session],
) -> None:
    rid = _add(session_factory, sender_domain="LOJA.com", category="promoções", action="trash")
    (m,) = RuleEngine(session_factory).match(make_email("a"), CLS)
    assert (m.rule.rule_id, m.rule.mode, m.action) == (rid, "suggest", "trash")


def test_all_conditions_must_hold(session_factory: sessionmaker[Session]) -> None:
    _add(session_factory, sender_domain="loja.com", category="Compras")
    assert RuleEngine(session_factory).match(make_email("a"), CLS) == ()


def test_sender_address_and_subject(session_factory: sessionmaker[Session]) -> None:
    _add(session_factory, sender_address="ofertas@loja.com", subject_contains="OFF")
    _add(session_factory, sender_address="outra@loja.com")
    assert len(RuleEngine(session_factory).match(make_email("a"), CLS)) == 1


def test_domain_does_not_match_suffix_of_other_domain(
    session_factory: sessionmaker[Session],
) -> None:
    _add(session_factory, sender_domain="oja.com")
    assert RuleEngine(session_factory).match(make_email("a"), CLS) == ()


def test_inactive_rule_ignored(session_factory: sessionmaker[Session]) -> None:
    _add(session_factory, sender_domain="loja.com", active=False)
    assert RuleEngine(session_factory).match(make_email("a"), CLS) == ()


def test_record_fired_counts(session_factory: sessionmaker[Session]) -> None:
    rid = _add(session_factory, sender_domain="loja.com")
    RuleEngine(session_factory).record_fired(rid)
    with session_factory() as s:
        assert s.get_one(AutomationRule, rid).fired_count == 1


def test_rule_without_conditions_rejected_by_db(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(IntegrityError):
        _add(session_factory)


def test_condition_unless_document() -> None:
    from src.agent.rules import RuleCondition

    cond = RuleCondition(sender_domain="loja.com", unless_document=True)
    assert cond.matches("Loja <a@loja.com>", "x", None, False)
    assert cond.matches("Loja <a@loja.com>", "x", None, None)
    assert not cond.matches("Loja <a@loja.com>", "x", None, True)
    assert not RuleCondition(category="Promoções").matches("a@b.com", "x", None, False)
    assert not RuleCondition().has_condition()
    assert RuleCondition(subject_contains="off").has_condition()


def test_existing_counts_and_caps(session_factory: sessionmaker[Session]) -> None:
    from src.agent.rules import RuleCondition
    from tests.chat_fakes import add_email

    for i in range(1, 6):
        add_email(session_factory, f"e{i}", received_day=i)
    add_email(session_factory, "nf", has_document=True, received_day=9)
    add_email(session_factory, "other", sender="x@outra.com", received_day=8)
    engine = RuleEngine(session_factory)
    cond = RuleCondition(sender_domain="loja.com", unless_document=True)
    ids, total = engine.existing(cond, 3)
    assert total == 5 and ids == ["e5", "e4", "e3"]
    assert engine.existing(cond, 0) == ([], 5)
    assert engine.existing(RuleCondition(category="promoções"), 0)[1] == 7


def test_engine_match_respects_unless_document(session_factory: sessionmaker[Session]) -> None:
    _add(session_factory, sender_domain="loja.com", unless_document=True)
    engine = RuleEngine(session_factory)
    assert len(engine.match(make_email("a"), CLS)) == 1
    with_doc = CLS.model_copy(update={"has_document": True})
    assert engine.match(make_email("a"), with_doc) == ()


def test_rule_admin_add_unless_document(session_factory: sessionmaker[Session]) -> None:
    from src.agent.rules import RuleAdmin

    admin = RuleAdmin(session_factory)
    rid = admin.add(name="lixo", action="trash", sender_domain="loja.com", unless_document=True,
                    source="chat")
    row = admin.get(rid)
    assert row is not None and row.unless_document and row.mode == "suggest"
