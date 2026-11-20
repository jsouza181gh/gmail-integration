"""Chat tools that show things or propose changes as cards.

None of them changes a rule, preference, policy or approval: each writes a
card on the turn in progress and the user confirms it in the panel
(`src.chat.cards.CardService`). `propose_email_actions` goes through the
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
    MailReader,
    PendingReader,
    RefSink,
    RuleReader,
    TaxonomySource,
)
from src.agent.rules import RuleCondition
from src.agent.state import StateUpdate
from src.agent.tools.mail import NoState
from src.agent.tools.refs import remember
from src.agent.types import (
    NEVER_AUTO,
    ActionKind,
    ActionsDraft,
    DocumentsDraft,
    EmailRef,
    EmailsDraft,
    KbScopeName,
    MailQuery,
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
BATCH_MAX = 50
SHARE_MAX = 5
PENDING_CARD_MAX = 50
KB_TEXT_MAX = 1000
WAITING = "aguardando confirmação do usuário no cartão"


class CardCtx(Deps):
    cards: CardSink
    chat: ChatScope


class RefCardCtx(CardCtx):
    refs: RefSink


class EmailsCtx(RefCardCtx):
    lookup: EmailLookup


class ShareCtx(CardCtx):
    docs: DocCatalog


class PendingCtx(CardCtx):
    pending: PendingReader


class ProposeActionCtx(RefCardCtx):
    actions: ActionSink
    lookup: EmailLookup
    mail: MailReader
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
    rows = [r for i in dict.fromkeys(email_ids[:SHOW_EMAILS_MAX]) if (r := ctx.lookup.ensure(i))]
    found = [r.id for r in rows]
    missing = [i for i in email_ids[:SHOW_EMAILS_MAX] if i not in found]
    remember(ctx.refs, ctx.chat.message_id,
             (EmailRef(email_id=r.id, subject=r.subject, sender=r.sender) for r in rows))
    if not found:
        raise ValueError(f"não existe mais no Gmail: {', '.join(missing)}")
    card = _card(ctx, EmailsDraft(email_ids=tuple(found)))
    note = f"; não existe mais no Gmail: {', '.join(missing)}" if missing else ""
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


def _batch_ids(ctx: ProposeActionCtx, email_ids: list[str] | None,
               gmail_query: str | None) -> list[str]:
    if (email_ids is None) == (gmail_query is None):
        raise ValueError("informe email_ids ou gmail_query (só um dos dois)")
    if email_ids is not None:
        return list(dict.fromkeys(email_ids))[:BATCH_MAX]
    ids: list[str] = []
    token: str | None = None
    while len(ids) < BATCH_MAX:
        page = ctx.mail.list_messages(MailQuery(
            query=gmail_query, max_results=BATCH_MAX - len(ids), page_token=token,
            include_spam_trash=True,
        ))
        ids += [i.id for i in page.items if i.id not in ids]
        token = page.next_page_token
        if token is None or not page.items:
            break
    return ids[:BATCH_MAX]


@tool(response_format="content_and_artifact", parse_docstring=True)
def propose_email_actions(
    kind: ActionKind,
    reason: str,
    runtime: ToolRuntime[ProposeActionCtx, NoState],
    email_ids: list[str] | None = None,
    gmail_query: str | None = None,
) -> tuple[str, StateUpdate | None]:
    """Suggest one action for several emails at once (up to 50), as a single
    card the user approves. Use it for any request about specific emails
    ("apaga esses", "arquiva aqueles"), including spam and trash.

    Args:
        kind: mark_read, star, archive, trash or block_sender.
        reason: Short reason in pt-BR, shown to the user.
        email_ids: Ids from list_emails, search_mail or an earlier answer.
        gmail_query: Instead of ids, a Gmail search (e.g. "in:spam from:x@y.com").
    """
    ctx = runtime.context
    action_ids: list[int] = []
    counts: Counter[str] = Counter()
    seen: list[EmailRef] = []
    for email_id in _batch_ids(ctx, email_ids, gmail_query):
        row = ctx.lookup.ensure(email_id)
        if row is None:
            counts["gone"] += 1
            continue
        seen.append(EmailRef(email_id=row.id, subject=row.subject, sender=row.sender))
        ref = ctx.actions.propose(email_id, kind, reason, run_id=ctx.run.run_id, trigger="chat")
        counts[ref.outcome] += 1
        if ref.outcome in ("suggested", "duplicate") and ref.action_id is not None:
            action_ids.append(ref.action_id)
    notes = [
        f"{label}: {counts[key]}"
        for key, label in (("duplicate", "já pendentes"), ("auto", "executados pela política"),
                           ("denied", "negados pela política"),
                           ("gone", "não existem mais no Gmail"))
        if counts[key]
    ]
    remember(ctx.refs, ctx.chat.message_id, seen)
    if not action_ids:
        raise ValueError("nenhuma sugestão criada; " + "; ".join(notes or ["nenhum e-mail"]))
    card = _card(ctx, ActionsDraft(action_ids=tuple(action_ids)))
    return "; ".join(
        [f"cartão #{card}: {kind} em {len(action_ids)} e-mail(s)", *notes, WAITING]
    ), None


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
