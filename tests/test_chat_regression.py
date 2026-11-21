"""Regression for the spam cleanup conversation of 2026-11-19 (runs 25-33).

Back then every tool the model chose failed on spam ids (only the INBOX is
stored), the model improvised a rule and sent the user to the panel, and a
confirmed card ended the conversation. Same script, real adapters.
"""
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.actions import ActionService
from src.agent.approvals import ApprovalService
from src.agent.config import DbAgentConfigs
from src.agent.context import AgentContext, ChatScope, RunScope
from src.agent.executor import ToolExecutor
from src.agent.flows.chat_turn import ChatTurnFlow
from src.agent.kb import DbKb
from src.agent.loop import AgentLoop
from src.agent.policy import PolicyResolver
from src.agent.prompts import CHAT_PROMPT_V2
from src.agent.rules import RuleAdmin
from src.agent.tools import build_registry
from src.agent.trail import Trail
from src.chat.retrieval import DbEmailLookup
from src.db.models import AgentConfig, Job
from src.jobs.store import JobStore
from src.jobs.types import ChatTurnJob, ClaimedJob
from src.llm.types import JsonValue, ToolCallRequest
from tests.agent_fakes import FakeTaxonomy, FakeToolCaller, make_ctx, turn
from tests.api_fakes import Api
from tests.chat_fakes import SpamMail, spam_item

SPAM = [f"sp{i}" for i in range(5)]


def _call(name: str, **args: JsonValue) -> ToolCallRequest:
    return ToolCallRequest(id=f"{name}-1", name=name, args=args)


def test_spam_cleanup_conversation(session_factory: sessionmaker[Session]) -> None:
    sf = session_factory
    with sf.begin() as s:
        s.add(AgentConfig(version=1, active=True, decide_prompt="p", chat_prompt=CHAT_PROMPT_V2,
                          max_turns=4, created_at=datetime.now(UTC)))
    api = Api(sf)
    api.login()
    store = api.deps.chat
    jobs = JobStore(sf)
    mail = SpamMail([spam_item(i, f"Alerta do Google - {i}") for i in SPAM])
    trail = Trail(sf)
    caller = FakeToolCaller([
        turn("", _call("list_emails", query="in:spam")),
        turn("Você tem 5 e-mails no spam. Quer ver?"),
        turn("", _call("show_emails", email_ids=list(SPAM))),
        turn("Aqui estão."),
        turn("", _call("propose_email_actions", kind="trash", reason="spam",
                       email_ids=list(SPAM))),
        turn("Preparei o cartão para apagar os 5."),
    ])

    def context(run: RunScope, chat: ChatScope) -> AgentContext:
        return make_ctx(
            run_id=run.run_id, trigger="chat", email_id=None, caller=caller,
            extra={"chat": chat, "mail": mail, "lookup": DbEmailLookup(sf, mail),
                   "actions": ActionService(sf, jobs, PolicyResolver(sf)), "cards": store,
                   "refs": store, "rules": RuleAdmin(sf), "taxonomy": FakeTaxonomy(),
                   "pending": ApprovalService(sf, jobs)},
        )

    flow = ChatTurnFlow(
        store=store, trail=trail,
        loop=AgentLoop(ToolExecutor(build_registry(), trail), trail, caller),
        configs=DbAgentConfigs(sf), kb=DbKb(sf), approvals=ApprovalService(sf, jobs),
        context_factory=context, structured=caller,  # type: ignore[arg-type]
    )
    conv = store.create_conversation().id

    def say(text: str) -> int:
        t = store.start_turn(conv, text=text)
        flow.handle(ClaimedJob(id=1, attempts=1, payload=ChatTurnJob(message_id=t.assistant.id)))
        done = store.turn(t.assistant.id).assistant
        assert done.status == "done", done.error
        return t.assistant.id

    say("Quantos e-mails de spam eu tenho para verificar se posso apagar?")
    shown = say("Mostre sim")
    (emails_card,) = store.turn(shown).assistant.cards
    assert emails_card.kind == "emails" and [i.email_id for i in emails_card.items] == SPAM

    trashed = say("Pode apagar esses e-mails para mim.")
    history = "\n".join(m.content for m in caller.prompts[-2].messages)
    assert "[e-mails desta resposta: sp0 — Alerta do Google - sp0" in history
    (actions_card,) = store.turn(trashed).assistant.cards
    assert actions_card.kind == "actions" and len(actions_card.items) == 5

    r = api.client.post(f"/api/chat/cards/{actions_card.id}/confirm", json={"verdict": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    msgs = store.messages(conv)
    assert msgs[-2].input == "event" and msgs[-1].progress == "apagando… 0/5"
    with sf() as s:
        kinds = [j.kind for j in s.scalars(select(Job).order_by(Job.id))]
    assert kinds.count("execute_action") == 5 and kinds[-1] == "execute_action"  # waits

    from src.chat.continuation import CardWaiter

    waiter = CardWaiter(store, jobs)
    for item in actions_card.items:
        assert item.action_id is not None
        api.deps.approvals.mark_executed(item.action_id)
        waiter.on_action_settled(item.action_id)
    msgs = store.messages(conv)
    assert msgs[-2].text == f"[evento] Lote do cartão #{actions_card.id} (trash) concluído: 5 executada(s)."
    with sf() as s:
        last = s.scalars(select(Job).order_by(Job.id.desc())).first()
    assert last is not None and (last.kind, last.chat_message_id) == ("chat_turn", msgs[-1].id)
