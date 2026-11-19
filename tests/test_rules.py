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
