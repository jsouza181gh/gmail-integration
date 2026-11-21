from src.chat.events import card_event
from src.chat.store import CardItemView, CardView, PolicyView, PreferenceView, PromoteView, RuleView


def _card(kind: str, **over: object) -> CardView:
    base: dict[str, object] = {
        "id": 7, "kind": kind, "status": "confirmed", "channel": "chat", "error": None,
        "rule": None, "preference": None, "promote": None, "policy": None, "verdict": None,
        "comment": None, "apply_existing": False, "affected": None, "created_rule_id": None,
        "kb_entry_id": None, "items": (),
    }
    base.update(over)
    return CardView.model_validate(base)


def _items(n: int) -> tuple[CardItemView, ...]:
    return tuple(CardItemView(email_id=None, action_id=i, attachment_id=None, title="t",
                              subtitle="s", status="approved") for i in range(n))


def test_card_event_texts() -> None:
    rule = RuleView(name="lixo da loja", action="trash", sender_address=None,
                    sender_domain="loja.com", category=None, subject_contains=None,
                    unless_document=False)
    assert card_event(_card("rule", rule=rule, created_rule_id=3)) == (
        "[evento] O usuário confirmou o cartão #7: regra 'lixo da loja' criada (#3)."
    )
    pref = PreferenceView(scope="decision", text="x")
    assert card_event(_card("preference", preference=pref)) == (
        "[evento] O usuário confirmou o cartão #7: preferência salva."
    )
    assert card_event(_card("actions", verdict="approve", items=_items(5)), ("trash",)) == (
        "[evento] O usuário aprovou 5 sugestão(ões) do cartão #7 (trash);"
        " a execução foi enfileirada."
    )
    assert card_event(_card("actions", verdict="reject", items=_items(2)), ("archive",)) == (
        "[evento] O usuário recusou 2 sugestão(ões) do cartão #7 (archive)."
    )
    promote = PromoteView(rule_id=3, name="lixo", action="trash", mode="suggest", fired=1,
                          approved=1, rejected=0)
    assert card_event(_card("promote", promote=promote)) == (
        "[evento] O usuário confirmou o cartão #7: regra #3 'lixo' passa a agir sozinha."
    )
    policy = PolicyView(target="trash", trigger="chat", policy="suggest")
    assert card_event(_card("policy", policy=policy)) == (
        "[evento] O usuário confirmou o cartão #7: política trash/chat = suggest."
    )
    assert card_event(_card("rule", status="failed", error="regra duplicada")) == (
        "[evento] O cartão #7 falhou: regra duplicada."
    )
