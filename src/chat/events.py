"""What the agent is told when the user acts on a card (the continuation turn)."""
from collections.abc import Sequence

from src.chat.store import CardView


def card_event(view: CardView, action_kinds: Sequence[str] = ()) -> str:
    """One `[evento]` line describing what confirming card `view` did."""
    head = f"[evento] O usuário confirmou o cartão #{view.id}:"
    if view.status == "failed":
        return f"[evento] O cartão #{view.id} falhou: {view.error or 'erro desconhecido'}."
    match view.kind:
        case "rule":
            name = view.rule.name if view.rule else ""
            return f"{head} regra '{name}' criada (#{view.created_rule_id})."
        case "preference":
            return f"{head} preferência salva."
        case "promote":
            p = view.promote
            return f"{head} regra #{p.rule_id} '{p.name}' passa a agir sozinha." if p else head
        case "policy":
            q = view.policy
            return f"{head} política {q.target}/{q.trigger} = {q.policy}." if q else head
        case "actions":
            kinds = f" ({', '.join(dict.fromkeys(action_kinds))})" if action_kinds else ""
            n = len(view.items)
            if view.verdict == "reject":
                return f"[evento] O usuário recusou {n} sugestão(ões) do cartão #{view.id}{kinds}."
            return (f"[evento] O usuário aprovou {n} sugestão(ões) do cartão #{view.id}{kinds};"
                    " a execução foi enfileirada.")
        case _:
            return f"{head} {view.kind}."

