"""Chat tools that show things or propose changes as cards.

None of them changes a rule, preference, policy or approval: each writes a
card on the turn in progress and the user confirms it in the panel
(`src.chat.cards.CardService`). `propose_email_action` goes through the
normal action sink, so it obeys the chat policies like any proposal.
"""
from collections import Counter
from email.utils import parseaddr

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from src.agent.context import ChatScope, Deps, RunScope
from src.agent.ports import (
    ActionSink,
    CardSink,
    DocCatalog,
    EmailLookup,
    PendingReader,
    RuleReader,
    TaxonomySource,
)
from src.agent.rules import RuleCondition
from src.agent.state import StateUpdate
from src.agent.tools.mail import NoState
from src.agent.types import (
    NEVER_AUTO,
    ActionKind,
    ActionsDraft,
    DocumentsDraft,
    EmailsDraft,
    KbScopeName,
    PolicyDecision,
    PolicyDraft,
    PolicyTarget,
    PreferenceDraft,
    PromoteDraft,
    RuleDraft,
    Trigger,
    Verdict,
    as_action_kind,
)

SHOW_EMAILS_MAX = 10
SHARE_MAX = 5
PENDING_CARD_MAX = 50
KB_TEXT_MAX = 1000
WAITING = "aguardando confirmação do usuário no cartão"


class CardCtx(Deps):
    cards: CardSink
    chat: ChatScope


class EmailsCtx(CardCtx):
    lookup: EmailLookup


class ShareCtx(CardCtx):
    docs: DocCatalog


class PendingCtx(CardCtx):
    pending: PendingReader


class ProposeActionCtx(CardCtx):
    actions: ActionSink
    lookup: EmailLookup
    run: RunScope


class RuleCardCtx(CardCtx):
    rules: RuleReader
    taxonomy: TaxonomySource


class PromoteCtx(CardCtx):
    rules: RuleReader


def _card(ctx: CardCtx, draft: ActionsDraft | EmailsDraft | DocumentsDraft | RuleDraft
          | PreferenceDraft | PromoteDraft | PolicyDraft) -> int:
    return ctx.cards.add_card(ctx.chat.message_id, ctx.chat.channel, draft)


@tool(response_format="content_and_artifact", parse_docstring=True)
def show_emails(
    email_ids: list[str], runtime: ToolRuntime[EmailsCtx, NoState]
) -> tuple[str, StateUpdate | None]:
    """Show emails to the user as a list they can open in the panel.

    Args:
        email_ids: Up to 10 ids from search_mail or list_emails.
    """
    ctx = runtime.context
    found = [i for i in dict.fromkeys(email_ids[:SHOW_EMAILS_MAX]) if ctx.lookup.email(i)]
    missing = [i for i in email_ids[:SHOW_EMAILS_MAX] if i not in found]
    if not found:
        raise ValueError("nenhum desses e-mails está guardado")
    card = _card(ctx, EmailsDraft(email_ids=tuple(found)))
    note = f"; não encontrados: {', '.join(missing)}" if missing else ""
    return f"cartão #{card}: {len(found)} e-mail(s) mostrado(s){note}", None


@tool(response_format="content_and_artifact", parse_docstring=True)
def share_documents(
    attachment_ids: list[int], runtime: ToolRuntime[ShareCtx, NoState]
) -> tuple[str, StateUpdate | None]:
    """Give the user download buttons for indexed documents.

    Args:
        attachment_ids: Up to 5 document ids from find_documents or search_mail.
    """
    ctx = runtime.context
    wanted = list(dict.fromkeys(attachment_ids[:SHARE_MAX]))
    ok = {d.id for d in ctx.docs.get(wanted)}
    found = [i for i in wanted if i in ok]
    if not found:
        raise ValueError("nenhum desses documentos está disponível para download")
    card = _card(ctx, DocumentsDraft(attachment_ids=tuple(found)))
    missing = [f"#{i}" for i in wanted if i not in ok]
    note = f"; indisponíveis: {', '.join(missing)}" if missing else ""
    return f"cartão #{card}: {len(found)} documento(s) para baixar{note}", None


@tool(response_format="content_and_artifact", parse_docstring=True)
def list_pending(
    runtime: ToolRuntime[PendingCtx, NoState],
    sender: str | None = None,
    kind: ActionKind | None = None,
) -> tuple[str, StateUpdate | None]:
    """Suggestions waiting for the user's approval, shown as a card with
    approve/reject buttons.

    Args:
        sender: Only suggestions for emails from this sender (part of the address).
        kind: Only this action (mark_read, star, archive, trash, block_sender).
    """
    ctx = runtime.context
    rows = ctx.pending.pending(sender=sender, kind=kind)
    if not rows:
        return "nenhuma sugestão pendente", None
    kinds = Counter(r.kind for r in rows)
    senders = Counter(parseaddr(r.sender or "")[1] or "?" for r in rows)
    card = _card(ctx, ActionsDraft(action_ids=tuple(r.id for r in rows[:PENDING_CARD_MAX])))
    by_kind = ", ".join(f"{k} {n}" for k, n in kinds.most_common())
    top = ", ".join(f"{s} ({n})" for s, n in senders.most_common(5))
    return (
        f"{len(rows)} pendente(s): {by_kind}. Remetentes: {top}. "
        f"Cartão #{card} mostra até {PENDING_CARD_MAX}."
    ), None


@tool(response_format="content_and_artifact", parse_docstring=True)
def decide_pending(
    action_ids: list[int],
    verdict: Verdict,
    runtime: ToolRuntime[PendingCtx, NoState],
    comment: str | None = None,
) -> tuple[str, StateUpdate | None]:
    """Prepare approving or rejecting pending suggestions; the user confirms
    on the card.

    Args:
        action_ids: Ids of the suggestions (from list_pending).
        verdict: approve or reject.
        comment: The user's reason, in the user's own words (becomes feedback).
    """
    ctx = runtime.context
    rows = ctx.pending.pending(ids=action_ids)
    if not rows:
        raise ValueError("nenhuma dessas sugestões está pendente")
    clean = (comment or "").strip()[:KB_TEXT_MAX] or None
    card = _card(ctx, ActionsDraft(action_ids=tuple(r.id for r in rows), verdict=verdict,
                                   comment=clean))
    word = "aprovar" if verdict == "approve" else "recusar"
    return f"cartão #{card}: {word} {len(rows)} sugestão(ões); {WAITING}", None


@tool(response_format="content_and_artifact", parse_docstring=True)
def propose_email_action(
    email_id: str, kind: ActionKind, reason: str, runtime: ToolRuntime[ProposeActionCtx, NoState]
) -> tuple[str, StateUpdate | None]:
    """Suggest an action on one email (it joins the approval queue).

    Args:
        email_id: Id from search_mail or list_emails.
        kind: mark_read, star, archive, trash or block_sender.
        reason: Short reason in pt-BR, shown to the user.
    """
    ctx = runtime.context
    if ctx.lookup.email(email_id) is None:
        raise ValueError(f"e-mail não encontrado: {email_id}")
    ref = ctx.actions.propose(email_id, kind, reason, run_id=ctx.run.run_id, trigger="chat")
    match ref.outcome:
        case "denied":
            return f"{kind}: negado pela política do chat", None
        case "auto":
            return f"{kind}: aprovado automaticamente pela política; será executado", None
        case _:
            assert ref.action_id is not None
            card = _card(ctx, ActionsDraft(action_ids=(ref.action_id,)))
            return f"{kind}: sugestão #{ref.action_id} no cartão #{card}; {WAITING}", None


def _canonical_category(taxonomy: TaxonomySource, category: str) -> str:
    active = taxonomy.active_taxonomy()
    names = active.names if active else []
    for name in names:
        if name.casefold() == category.strip().casefold():
            return name
    raise ValueError(f"categoria desconhecida; use uma de: {', '.join(names)}")


@tool(response_format="content_and_artifact", parse_docstring=True)
def propose_rule(
    name: str,
    action: ActionKind,
    runtime: ToolRuntime[RuleCardCtx, NoState],
    sender_address: str | None = None,
    sender_domain: str | None = None,
    category: str | None = None,
    subject_contains: str | None = None,
    unless_document: bool = False,
) -> tuple[str, StateUpdate | None]:
    """Propose an automation rule (deterministic condition -> action). It is
    created only when the user confirms the card, always in "suggest" mode.

    Args:
        name: Short name in pt-BR.
        action: mark_read, star, archive, trash or block_sender.
        sender_address: Exact sender address.
        sender_domain: Sender domain, e.g. loja.com.br.
        category: One of the taxonomy categories.
        subject_contains: Text the subject must contain.
        unless_document: Skip emails that carry a document (nota fiscal, boleto...).
    """
    ctx = runtime.context
    cond = RuleCondition(
        sender_address=parseaddr(sender_address)[1].lower() or None if sender_address else None,
        sender_domain=sender_domain.strip().lstrip("@").lower() or None if sender_domain else None,
        category=_canonical_category(ctx.taxonomy, category) if category else None,
        subject_contains=subject_contains.strip() or None if subject_contains else None,
        unless_document=unless_document,
    )
    if not cond.has_condition():
        raise ValueError("a regra precisa de remetente, domínio, categoria ou assunto")
    _, affected = ctx.rules.existing(cond, 0)
    card = _card(ctx, RuleDraft(
        name=name.strip()[:120] or "regra do chat",
        action=action,
        sender_address=cond.sender_address,
        sender_domain=cond.sender_domain,
        category=cond.category,
        subject_contains=cond.subject_contains,
        unless_document=unless_document,
        affected=affected,
    ))
    return f"cartão #{card}: regra afeta ~{affected} e-mail(s) já guardados; {WAITING}", None


@tool(response_format="content_and_artifact", parse_docstring=True)
def propose_preference(
    text: str, scope: KbScopeName, runtime: ToolRuntime[CardCtx, NoState]
) -> tuple[str, StateUpdate | None]:
    """Propose a preference in free text for the knowledge base (when the
    condition is not deterministic).

    Args:
        text: The preference in pt-BR, in the user's words.
        scope: classification (how to categorize) or decision (what to propose).
    """
    clean = text.strip()[:KB_TEXT_MAX]
    if not clean:
        raise ValueError("preferência vazia")
    card = _card(runtime.context, PreferenceDraft(scope=scope, text=clean))
    return f"cartão #{card}: preferência; {WAITING}", None


@tool(response_format="content_and_artifact", parse_docstring=True)
def propose_promotion(
    rule_id: int, runtime: ToolRuntime[PromoteCtx, NoState]
) -> tuple[str, StateUpdate | None]:
    """Propose that a rule acts on its own (mode auto). The card shows its
    history (fired, approved, rejected).

    Args:
        rule_id: Id from list_rules.
    """
    ctx = runtime.context
    rule = ctx.rules.get(rule_id)
    if rule is None:
        raise ValueError(f"regra #{rule_id} não existe")
    if not rule.active:
        raise ValueError("a regra está desativada")
    if rule.mode == "auto":
        raise ValueError("a regra já age sozinha")
    kind = as_action_kind(rule.action)
    if kind is None or kind in NEVER_AUTO:
        raise ValueError(f"'{rule.action}' nunca pode agir sozinha")
    card = _card(ctx, PromoteDraft(rule_id=rule_id))
    return f"cartão #{card}: promover regra #{rule_id}; {WAITING}", None


@tool(response_format="content_and_artifact", parse_docstring=True)
def propose_policy(
    target: PolicyTarget,
    trigger: Trigger,
    policy: PolicyDecision,
    runtime: ToolRuntime[CardCtx, NoState],
) -> tuple[str, StateUpdate | None]:
    """Propose changing whether an action runs on its own, waits for approval
    or is never proposed.

    Args:
        target: mark_read, star, archive, trash, block_sender or apply_labels.
        trigger: webhook (new email) or chat.
        policy: auto, suggest or deny.
    """
    if policy == "auto" and target in NEVER_AUTO:
        raise ValueError(f"'{target}' nunca pode ficar em auto")
    card = _card(runtime.context, PolicyDraft(target=target, trigger=trigger, policy=policy))
    return f"cartão #{card}: política {target}/{trigger} = {policy}; {WAITING}", None
