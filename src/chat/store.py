"""Chat persistence: conversations, turns, the original audio and cards.

The API starts turns and reads them; the worker's chat flow moves the
assistant message through queued -> running -> done | failed and adds the
cards its tools propose (`add_card` satisfies `ports.CardSink`). Every
method is its own transaction.
"""
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.prompts import HistoryTurn
from src.agent.types import (
    EmailRef,
    ActionsDraft,
    CardDraft,
    ChatChannel,
    DocumentsDraft,
    EmailsDraft,
    PolicyDraft,
    PreferenceDraft,
    PromoteDraft,
    RuleDraft,
)
from src.chat.audio import WavInfo
from src.chat.batches import batch_done_event, progress_text
from src.db.models import (
    LlmCall,
    ChatMessageRef,
    Attachment,
    AutomationRule,
    ChatAudio,
    ChatCard,
    ChatCardItem,
    ChatConversation,
    ChatMessage,
    Email,
    SuggestedAction,
)
from src.llm.types import Schema

TEXT_MAX = 4000
TITLE_MAX = 60
HISTORY_LIMIT = 20
REFS_MAX = 20  # stored per answer
REFS_SHOWN = 10  # rendered in the history
LIVE = ("queued", "running")
INFO_KINDS = frozenset({"emails", "documents"})
STATUS_PT = {
    "proposed": "aguardando confirmação",
    "confirmed": "confirmado",
    "cancelled": "cancelado",
    "failed": "falhou",
    "info": "mostrado",
}


class ChatConflict(Exception):
    """The request does not fit the current state (409)."""


class TurnBusy(ChatConflict):
    """The conversation already has a turn in flight."""


class CardConflict(ChatConflict):
    """The card was already decided or cannot be decided."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ConversationRow(Schema):
    id: int
    title: str | None
    updated_at: datetime
    busy: bool


class CardItemView(Schema):
    email_id: str | None
    action_id: int | None
    attachment_id: int | None
    title: str
    subtitle: str
    status: str | None  # suggestion status for action items


class RuleView(Schema):
    name: str
    action: str
    sender_address: str | None
    sender_domain: str | None
    category: str | None
    subject_contains: str | None
    unless_document: bool


class PreferenceView(Schema):
    scope: str
    text: str


class PromoteView(Schema):
    rule_id: int
    name: str
    action: str
    mode: str
    fired: int
    approved: int
    rejected: int


class PolicyView(Schema):
    target: str
    trigger: str
    policy: str


class CardView(Schema):
    id: int
    kind: str
    status: str
    channel: str
    error: str | None
    rule: RuleView | None
    preference: PreferenceView | None
    promote: PromoteView | None
    policy: PolicyView | None
    verdict: str | None
    comment: str | None
    apply_existing: bool
    affected: int | None
    created_rule_id: int | None
    kb_entry_id: int | None
    items: tuple[CardItemView, ...]
    batch_query: str | None = None
    batch_total: int | None = None
    batch_no: int | None = None


class CallFailure(Schema):
    """One failed model call of an answer (the panel toasts them)."""

    model: str
    reason: str


def failure_reason(kind: str | None, error: str | None) -> str:
    text = error or ""
    if "Timeout" in text or "timed out" in text:
        return "não respondeu a tempo"
    if "503" in text or "UNAVAILABLE" in text:
        return "sobrecarregado (503)"
    if "504" in text or "DEADLINE" in text:
        return "não respondeu a tempo"
    if kind == "rate" or "429" in text:
        return "limite de uso (429)"
    if kind == "bad_output":
        return "resposta inválida"
    if kind == "transient":
        return "falha de conexão"
    return "erro"


class MessageView(Schema):
    id: int
    conversation_id: int
    role: str
    text: str
    input: str
    status: str
    progress: str | None
    error: str | None
    audio_ms: int | None
    run_id: int | None
    created_at: datetime
    cards: tuple[CardView, ...]
    model: str | None = None
    failures: tuple[CallFailure, ...] = ()


class TurnView(Schema):
    user: MessageView
    assistant: MessageView


def _size(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


class ChatStore:
    def __init__(
        self, session_factory: sessionmaker[Session], clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self.sf = session_factory
        self._now = clock

    # --- conversations ------------------------------------------------------

    def create_conversation(self) -> ConversationRow:
        now = self._now()
        with self.sf.begin() as s:
            conv = ChatConversation(title=None, created_at=now, updated_at=now)
            s.add(conv)
            s.flush()
            return ConversationRow(id=conv.id, title=None, updated_at=now, busy=False)

    def conversations(self, limit: int) -> list[ConversationRow]:
        with self.sf() as s:
            convs = s.scalars(
                select(ChatConversation)
                .order_by(ChatConversation.updated_at.desc(), ChatConversation.id.desc())
                .limit(limit)
            ).all()
            busy = set(
                s.scalars(
                    select(ChatMessage.conversation_id).where(
                        ChatMessage.conversation_id.in_([c.id for c in convs]),
                        ChatMessage.role == "assistant",
                        ChatMessage.status.in_(LIVE),
                    )
                )
            )
            return [
                ConversationRow(id=c.id, title=c.title, updated_at=c.updated_at, busy=c.id in busy)
                for c in convs
            ]

    # --- turns ----------------------------------------------------------------

    def _busy(self, s: Session, conversation_id: int) -> bool:
        found = s.scalar(
            select(ChatMessage.id).where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.role == "assistant",
                ChatMessage.status.in_(LIVE),
            ).limit(1)
        )
        return found is not None

    def start_turn(
        self,
        conversation_id: int,
        *,
        text: str | None = None,
        audio: tuple[bytes, WavInfo] | None = None,
        model: str | None = None,
    ) -> TurnView:
        """The user's message plus a queued assistant placeholder."""
        clean = (text or "").strip()[:TEXT_MAX]
        if audio is None and not clean:
            raise ValueError("mensagem vazia")
        now = self._now()
        with self.sf.begin() as s:
            conv = s.get(ChatConversation, conversation_id)
            if conv is None:
                raise LookupError("conversa não encontrada")
            if self._busy(s, conversation_id):
                raise TurnBusy("espere a resposta anterior")
            user = ChatMessage(
                conversation_id=conversation_id,
                role="user",
                text="" if audio else clean,
                input="audio" if audio else "text",
                status="queued" if audio else "done",
                model=model,
                created_at=now,
                updated_at=now,
            )
            s.add(user)
            s.flush()
            if audio is not None:
                data, info = audio
                s.add(ChatAudio(message_id=user.id, mime_type="audio/wav", data=data,
                                duration_ms=info.duration_ms, created_at=now))
            assistant_id = self._placeholder(s, conversation_id, user.id, now)
            if conv.title is None and clean:
                conv.title = clean[:TITLE_MAX]
            conv.updated_at = now
        return self.turn(assistant_id)

    def start_event_turn(
        self, conversation_id: int, text: str, wait_card_id: int | None = None
    ) -> TurnView | None:
        """An `[evento]` message plus a queued answer; None when the conversation
        is busy (the user already moved on). With `wait_card_id` the answer
        waits for that card's approved actions (settle_card releases it)."""
        now = self._now()
        with self.sf.begin() as s:
            conv = s.get(ChatConversation, conversation_id)
            if conv is None:
                raise LookupError("conversa não encontrada")
            if self._busy(s, conversation_id):
                return None
            pinned = s.scalar(  # the continuation keeps the model the user chose
                select(ChatMessage.model)
                .where(ChatMessage.conversation_id == conversation_id,
                       ChatMessage.role == "user", ChatMessage.input != "event")
                .order_by(ChatMessage.id.desc()).limit(1)
            )
            event = ChatMessage(conversation_id=conversation_id, role="user",
                                text=text[:TEXT_MAX], input="event", status="done",
                                model=pinned, created_at=now, updated_at=now)
            s.add(event)
            s.flush()
            assistant_id = self._placeholder(s, conversation_id, event.id, now)
            if wait_card_id is not None:
                s.get_one(ChatMessage, assistant_id).wait_card_id = wait_card_id
            conv.updated_at = now
        return self.turn(assistant_id)

    def cards_of_action(self, action_id: int) -> list[int]:
        """Cards holding this action that have a continuation waiting on them."""
        with self.sf() as s:
            return list(s.scalars(
                select(ChatCardItem.card_id)
                .join(ChatMessage, ChatMessage.wait_card_id == ChatCardItem.card_id)
                .where(ChatCardItem.action_id == action_id)
                .distinct()
            ))

    def settle_card(self, card_id: int) -> int | None:
        """Progress of the continuation waiting on `card_id`; once no action of
        the card is still approved (each executed or failed), the event gets the
        real results and the waiting answer's id is returned to be enqueued."""
        with self.sf.begin() as s:
            waiting = s.scalars(
                select(ChatMessage).where(ChatMessage.wait_card_id == card_id,
                                          ChatMessage.role == "assistant",
                                          ChatMessage.status == "queued")
            ).first()
            if waiting is None:
                return None
            card = s.get_one(ChatCard, card_id)
            actions = list(s.scalars(
                select(SuggestedAction)
                .join(ChatCardItem, ChatCardItem.action_id == SuggestedAction.id)
                .where(ChatCardItem.card_id == card_id)
                .order_by(ChatCardItem.position)
            ))
            kind = card.action or (actions[0].kind if actions else "trash")
            executed = sum(a.status == "executed" for a in actions)
            failed = [a for a in actions if a.status == "failed"]
            if any(a.status == "approved" for a in actions):
                waiting.progress = progress_text(kind, executed + len(failed), len(actions))
                waiting.updated_at = self._now()
                return None
            if waiting.reply_to is not None:
                s.get_one(ChatMessage, waiting.reply_to).text = batch_done_event(
                    card_id, kind, executed, len(failed), failed[0].error if failed else None,
                    card.batch_query, card.batch_total, card.batch_no,
                )
            waiting.wait_card_id, waiting.progress = None, None
            waiting.updated_at = self._now()
            return waiting.id

    def card_conversation(self, card_id: int) -> int | None:
        with self.sf() as s:
            return s.scalar(
                select(ChatMessage.conversation_id)
                .join(ChatCard, ChatCard.message_id == ChatMessage.id)
                .where(ChatCard.id == card_id)
            )

    def card_action_kinds(self, card_id: int) -> tuple[str, ...]:
        with self.sf() as s:
            return tuple(s.scalars(
                select(SuggestedAction.kind)
                .join(ChatCardItem, ChatCardItem.action_id == SuggestedAction.id)
                .where(ChatCardItem.card_id == card_id)
                .order_by(ChatCardItem.position)
            ))

    def _placeholder(self, s: Session, conversation_id: int, user_id: int, now: datetime) -> int:
        bot = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            text="",
            input="text",
            status="queued",
            reply_to=user_id,
            created_at=now,
            updated_at=now,
        )
        s.add(bot)
        s.flush()
        return bot.id

    def retry(self, assistant_id: int) -> TurnView:
        """A new placeholder answering the same user message as a failed one."""
        now = self._now()
        with self.sf.begin() as s:
            old = s.get(ChatMessage, assistant_id)
            if old is None or old.role != "assistant":
                raise LookupError("mensagem não encontrada")
            if old.status != "failed" or old.reply_to is None:
                raise ChatConflict("só dá para repetir uma resposta que falhou")
            if self._busy(s, old.conversation_id):
                raise TurnBusy("espere a resposta anterior")
            new_id = self._placeholder(s, old.conversation_id, old.reply_to, now)
            s.get_one(ChatConversation, old.conversation_id).updated_at = now
        return self.turn(new_id)

    def assistant_conversation(self, assistant_id: int) -> int | None:
        with self.sf() as s:
            m = s.get(ChatMessage, assistant_id)
            return m.conversation_id if m is not None and m.role == "assistant" else None

    # --- worker side ------------------------------------------------------------

    def begin(self, assistant_id: int) -> bool:
        with self.sf.begin() as s:
            m = s.get(ChatMessage, assistant_id)
            if m is None or m.status != "queued":
                return False
            m.status, m.progress, m.updated_at = "running", "pensando…", self._now()
            return True

    def progress(self, assistant_id: int, label: str) -> None:
        with self.sf.begin() as s:
            m = s.get_one(ChatMessage, assistant_id)
            m.progress, m.updated_at = label[:120], self._now()

    def set_transcript(self, user_id: int, text: str) -> None:
        now = self._now()
        with self.sf.begin() as s:
            m = s.get_one(ChatMessage, user_id)
            m.text, m.status, m.updated_at = text.strip()[:TEXT_MAX], "done", now
            conv = s.get_one(ChatConversation, m.conversation_id)
            if conv.title is None and m.text:
                conv.title = m.text[:TITLE_MAX]

    def attach_run(self, assistant_id: int, run_id: int) -> None:
        with self.sf.begin() as s:
            s.get_one(ChatMessage, assistant_id).run_id = run_id

    def _close(self, assistant_id: int, status: str, text: str, error: str | None) -> None:
        now = self._now()
        with self.sf.begin() as s:
            m = s.get_one(ChatMessage, assistant_id)
            m.status, m.text, m.error, m.progress, m.updated_at = status, text, error, None, now
            if m.reply_to is not None:
                user = s.get_one(ChatMessage, m.reply_to)
                if user.status == "queued":
                    user.status, user.updated_at = "done", now
            s.get_one(ChatConversation, m.conversation_id).updated_at = now

    def finish(self, assistant_id: int, text: str) -> None:
        self._close(assistant_id, "done", text, None)

    def fail(self, assistant_id: int, reason: str) -> None:
        self._close(assistant_id, "failed", "", reason[:500])

    def history(
        self, conversation_id: int, before_id: int, limit: int = HISTORY_LIMIT
    ) -> list[HistoryTurn]:
        """Finished messages before `before_id`, oldest first, with card outcomes."""
        with self.sf() as s:
            rows = s.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.id < before_id,
                    ChatMessage.status == "done",
                )
                .order_by(ChatMessage.id.desc())
                .limit(limit)
            ).all()
            answers = [m.id for m in rows if m.role == "assistant"]
            cards = self._cards(s, answers)
            refs: dict[int, list[ChatMessageRef]] = {}
            for r in s.scalars(
                select(ChatMessageRef).where(ChatMessageRef.message_id.in_(answers))
                .order_by(ChatMessageRef.message_id, ChatMessageRef.position)
            ):
                refs.setdefault(r.message_id, []).append(r)
        lines: list[HistoryTurn] = []
        for m in reversed(rows):
            text = m.text
            notes = [
                f"[cartão #{c.id} {self._summary_of(c)}: {STATUS_PT.get(c.status, c.status)}]"
                for c in cards.get(m.id, ())
            ]
            if shown := refs.get(m.id, [])[:REFS_SHOWN]:
                notes.append("[e-mails desta resposta: " + "; ".join(
                    f"{r.email_id} — {r.subject or '(sem assunto)'} ({r.sender or '-'})"
                    for r in shown
                ) + "]")
            if notes:
                text = "\n".join([text, *notes]) if text else "\n".join(notes)
            if text:
                lines.append(HistoryTurn(role="user" if m.role == "user" else "assistant",
                                         text=text))
        return lines

    def add_refs(self, message_id: int, refs: Sequence[EmailRef]) -> None:
        """Remember the emails an answer referenced: first mention wins, at most REFS_MAX."""
        with self.sf.begin() as s:
            have = list(s.scalars(
                select(ChatMessageRef.email_id).where(ChatMessageRef.message_id == message_id)
            ))
            for ref in refs:
                if len(have) >= REFS_MAX:
                    break
                if ref.email_id in have:
                    continue
                s.add(ChatMessageRef(message_id=message_id, position=len(have),
                                     email_id=ref.email_id, subject=ref.subject,
                                     sender=ref.sender))
                have.append(ref.email_id)

    # --- cards ------------------------------------------------------------------

    def batch_count(self, message_id: int, query: str, action: str) -> int:
        """Confirmed actions cards of this conversation for `query` (batches done)."""
        with self.sf() as s:
            conv = s.scalar(select(ChatMessage.conversation_id).where(ChatMessage.id == message_id))
            return s.scalar(
                select(func.count()).select_from(ChatCard)
                .join(ChatMessage, ChatMessage.id == ChatCard.message_id)
                .where(ChatMessage.conversation_id == conv, ChatCard.kind == "actions",
                       ChatCard.status == "confirmed",
                       ChatCard.batch_query == query, ChatCard.action == action)
            ) or 0

    def add_card(self, message_id: int, channel: ChatChannel, draft: CardDraft) -> int:
        now = self._now()
        card = ChatCard(
            message_id=message_id,
            kind=draft.kind,
            status="info" if draft.kind in INFO_KINDS else "proposed",
            channel=channel,
            created_at=now,
        )
        items: Sequence[tuple[str | None, int | None, int | None]] = ()
        match draft:
            case RuleDraft():
                card.rule_name = draft.name[:120]
                card.action = draft.action
                card.sender_address = draft.sender_address
                card.sender_domain = draft.sender_domain
                card.category = draft.category
                card.subject_contains = draft.subject_contains
                card.unless_document = draft.unless_document
                card.affected = draft.affected
            case PreferenceDraft():
                card.kb_scope, card.kb_text = draft.scope, draft.text
            case PromoteDraft():
                card.rule_id = draft.rule_id
            case PolicyDraft():
                card.policy_target = draft.target
                card.policy_trigger = draft.trigger
                card.policy_value = draft.policy
            case ActionsDraft():
                card.verdict, card.comment = draft.verdict, draft.comment
                card.action = draft.action
                card.batch_query = draft.batch_query
                card.batch_total, card.batch_no = draft.batch_total, draft.batch_no
                items = [(None, a, None) for a in draft.action_ids]
            case EmailsDraft():
                items = [(e, None, None) for e in draft.email_ids]
            case DocumentsDraft():
                items = [(None, None, a) for a in draft.attachment_ids]
        with self.sf.begin() as s:
            s.add(card)
            s.flush()
            for pos, (email_id, action_id, attachment_id) in enumerate(items):
                s.add(ChatCardItem(card_id=card.id, position=pos, email_id=email_id,
                                   action_id=action_id, attachment_id=attachment_id))
            return card.id

    def card(self, card_id: int) -> CardView | None:
        with self.sf() as s:
            row = s.get(ChatCard, card_id)
            if row is None:
                return None
            return self._views(s, [row])[0]

    def _summary_of(self, c: CardView) -> str:
        match c.kind:
            case "rule":
                return f"regra '{c.rule.name if c.rule else ''}'"
            case "preference":
                return f"preferência '{(c.preference.text if c.preference else '')[:80]}'"
            case "promote":
                return f"promover regra #{c.promote.rule_id if c.promote else '?'}"
            case "policy":
                p = c.policy
                return f"política {p.target}/{p.trigger} = {p.policy}" if p else "política"
            case "actions":
                verdict = {"approve": "aprovar", "reject": "recusar"}.get(c.verdict or "", "decidir")
                return f"{verdict} {len(c.items)} sugestão(ões)"
            case "emails":
                return f"{len(c.items)} e-mail(s)"
            case _:
                return f"{len(c.items)} documento(s)"

    def _cards(self, s: Session, message_ids: Sequence[int]) -> dict[int, list[CardView]]:
        if not message_ids:
            return {}
        rows = s.scalars(
            select(ChatCard).where(ChatCard.message_id.in_(list(message_ids))).order_by(ChatCard.id)
        ).all()
        out: dict[int, list[CardView]] = {}
        for row, view in zip(rows, self._views(s, rows), strict=True):
            out.setdefault(row.message_id, []).append(view)
        return out

    def _views(self, s: Session, rows: Sequence[ChatCard]) -> list[CardView]:
        ids = [r.id for r in rows]
        items = s.scalars(
            select(ChatCardItem)
            .where(ChatCardItem.card_id.in_(ids))
            .order_by(ChatCardItem.card_id, ChatCardItem.position)
        ).all()
        resolved = self._resolve_items(s, items)
        by_card: dict[int, list[CardItemView]] = {}
        for item, view in zip(items, resolved, strict=True):
            by_card.setdefault(item.card_id, []).append(view)
        rule_ids = [r.rule_id for r in rows if r.rule_id is not None]
        rules = {
            r.id: r
            for r in s.scalars(select(AutomationRule).where(AutomationRule.id.in_(rule_ids)))
        }
        views: list[CardView] = []
        for c in rows:
            promoted = rules.get(c.rule_id) if c.rule_id is not None else None
            views.append(CardView(
                id=c.id,
                kind=c.kind,
                status=c.status,
                channel=c.channel,
                error=c.error,
                rule=RuleView(
                    name=c.rule_name or "", action=c.action or "",
                    sender_address=c.sender_address, sender_domain=c.sender_domain,
                    category=c.category, subject_contains=c.subject_contains,
                    unless_document=bool(c.unless_document),
                ) if c.kind == "rule" else None,
                preference=PreferenceView(scope=c.kb_scope or "", text=c.kb_text or "")
                if c.kind == "preference" else None,
                promote=PromoteView(
                    rule_id=promoted.id, name=promoted.name, action=promoted.action,
                    mode=promoted.mode, fired=promoted.fired_count,
                    approved=promoted.approved_count, rejected=promoted.rejected_count,
                ) if promoted is not None and c.kind == "promote" else None,
                policy=PolicyView(target=c.policy_target or "", trigger=c.policy_trigger or "",
                                  policy=c.policy_value or "")
                if c.kind == "policy" else None,
                verdict=c.verdict,
                comment=c.comment,
                apply_existing=c.apply_existing,
                affected=c.affected,
                created_rule_id=c.created_rule_id,
                kb_entry_id=c.kb_entry_id,
                items=tuple(by_card.get(c.id, ())),
                batch_query=c.batch_query, batch_total=c.batch_total, batch_no=c.batch_no,
            ))
        return views

    def _resolve_items(self, s: Session, items: Sequence[ChatCardItem]) -> list[CardItemView]:
        email_ids = {i.email_id for i in items if i.email_id is not None}
        action_ids = [i.action_id for i in items if i.action_id is not None]
        attachment_ids = [i.attachment_id for i in items if i.attachment_id is not None]
        actions = {a.id: a for a in s.scalars(
            select(SuggestedAction).where(SuggestedAction.id.in_(action_ids)))}
        email_ids |= {a.email_id for a in actions.values()}
        emails = {e.id: e for e in s.scalars(select(Email).where(Email.id.in_(email_ids)))}
        docs = {a.id: a for a in s.scalars(
            select(Attachment).where(Attachment.id.in_(attachment_ids)))}
        out: list[CardItemView] = []
        for i in items:
            title, subtitle, status = "(não encontrado)", "", None
            if i.email_id is not None and (e := emails.get(i.email_id)) is not None:
                title, subtitle = e.subject or "(sem assunto)", e.sender or ""
            elif i.action_id is not None and (a := actions.get(i.action_id)) is not None:
                e = emails.get(a.email_id)
                title = (e.subject if e else None) or "(sem assunto)"
                subtitle = f"{a.kind} · {(e.sender if e else None) or ''}"
                status = a.status
            elif i.attachment_id is not None and (d := docs.get(i.attachment_id)) is not None:
                title = d.title or d.filename
                subtitle = f"{d.doc_type or d.mime_type} · {_size(d.size)}"
            out.append(CardItemView(email_id=i.email_id, action_id=i.action_id,
                                    attachment_id=i.attachment_id, title=title,
                                    subtitle=subtitle, status=status))
        return out

    # --- reads ------------------------------------------------------------------

    def _message_views(self, s: Session, rows: Sequence[ChatMessage]) -> list[MessageView]:
        cards = self._cards(s, [m.id for m in rows if m.role == "assistant"])
        audio = {
            message_id: ms
            for message_id, ms in s.execute(
                select(ChatAudio.message_id, ChatAudio.duration_ms)
                .where(ChatAudio.message_id.in_([m.id for m in rows]))
            )
        }
        runs = [m.run_id for m in rows if m.role == "assistant" and m.run_id is not None]
        failures: dict[int, list[CallFailure]] = {}
        for c in s.scalars(
            select(LlmCall).where(LlmCall.agent_run_id.in_(runs), LlmCall.status == "error")
            .order_by(LlmCall.id)
        ):
            if c.agent_run_id is not None:
                failures.setdefault(c.agent_run_id, []).append(
                    CallFailure(model=c.model, reason=failure_reason(c.error_kind, c.error))
                )
        return [
            MessageView(
                id=m.id, conversation_id=m.conversation_id, role=m.role, text=m.text,
                input=m.input, status=m.status, progress=m.progress, error=m.error,
                audio_ms=audio.get(m.id), run_id=m.run_id, created_at=m.created_at,
                cards=tuple(cards.get(m.id, ())), model=m.model,
                failures=tuple(failures.get(m.run_id, ())) if m.run_id is not None else (),
            )
            for m in rows
        ]

    def messages(self, conversation_id: int) -> list[MessageView]:
        with self.sf() as s:
            if s.get(ChatConversation, conversation_id) is None:
                raise LookupError("conversa não encontrada")
            rows = s.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.id)
            ).all()
            return self._message_views(s, rows)

    def turn(self, assistant_id: int) -> TurnView:
        with self.sf() as s:
            bot = s.get(ChatMessage, assistant_id)
            if bot is None or bot.role != "assistant" or bot.reply_to is None:
                raise LookupError("mensagem não encontrada")
            user = s.get_one(ChatMessage, bot.reply_to)
            u, a = self._message_views(s, [user, bot])
            return TurnView(user=u, assistant=a)

    def audio(self, message_id: int) -> tuple[str, bytes] | None:
        with self.sf() as s:
            row = s.get(ChatAudio, message_id)
            return None if row is None else (row.mime_type, row.data)
