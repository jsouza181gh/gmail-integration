"""Builds the real agent: one gateway, the Gmail adapters and the DB adapters.

Everything process-wide (gateway pacing state, registry) is created once;
Gmail services are created per call because googleapiclient is not
thread-safe and tokens refresh.
"""
import os

from src.agent.actions import ActionService
from src.agent.approvals import ApprovalService
from src.agent.config import DbAgentConfigs
from src.agent.context import AgentContext, ChatScope, LLMDeps, RunScope
from src.agent.executor import ToolExecutor
from src.agent.flows.chat_turn import ChatTurnFlow
from src.agent.flows.execute_action import ExecuteActionFlow
from src.agent.flows.process_email import ProcessEmailFlow
from src.agent.kb import DbKb
from src.agent.loop import AgentLoop
from src.agent.policy import PolicyResolver
from src.agent.rules import RuleAdmin, RuleEngine
from src.agent.sender_history import DbSenderHistory
from src.agent.tools import build_registry
from src.agent.trail import Trail
from src.chat.continuation import CardWaiter
from src.chat.retrieval import DbDocCatalog, DbEmailLookup, DbRetriever
from src.chat.store import ChatStore
from src.db.store import Store
from src.gmail.actions import GmailActions
from src.gmail.client import get_gmail_service, has_scope
from src.gmail.labels import GmailLabeler
from src.gmail.reader import GmailReader
from src.indexing.store import IndexStore
from src.jobs.store import JobStore
from src.llm.config import build_chat_gateway, build_gateway
from src.llm.gateway import LLMGateway
from src.llm.recorder import DbCallRecorder


class Agent:
    """The wired agent pieces a worker needs."""

    def __init__(
        self,
        store: Store,
        jobs: JobStore,
        gateway: LLMGateway | None = None,
        chat_gateway: LLMGateway | None = None,
    ) -> None:
        sf = store.sf
        gateway = gateway or build_gateway(DbCallRecorder(sf))
        chat_gateway = chat_gateway or build_chat_gateway(DbCallRecorder(sf))
        self.trail = Trail(sf)
        self.policy = PolicyResolver(sf)
        self.actions = ActionService(sf, jobs, self.policy)
        self.executor = ToolExecutor(build_registry(), self.trail)
        kb = DbKb(sf)
        mail = GmailReader(get_gmail_service)
        labels = GmailLabeler(get_gmail_service, sf, dry_run=os.getenv("LABELS_DRY_RUN") == "1")
        history = DbSenderHistory(sf)
        self.approvals = ApprovalService(sf, jobs)
        self.chat_store = ChatStore(sf)
        retriever = DbRetriever(IndexStore(sf), gateway)
        lookup = DbEmailLookup(sf, mail)
        docs = DbDocCatalog(sf)
        rules = RuleAdmin(sf)

        def build_context(
            run: RunScope, email_id: str | None, chat: ChatScope | None
        ) -> AgentContext:
            return AgentContext(
                run=run,
                email_id=email_id,
                chat=chat,
                llm=LLMDeps(structured=gateway, tools=gateway),
                taxonomy=store,
                mail=mail,
                labels=labels,
                history=history,
                actions=self.actions,
                policy=self.policy,
                kb=kb,
                retriever=retriever,
                lookup=lookup,
                docs=docs,
                pending=self.approvals,
                rules=rules,
                cards=self.chat_store,
                refs=self.chat_store,
            )

        def context_factory(run: RunScope, email_id: str) -> AgentContext:
            return build_context(run, email_id, None)

        def chat_context(run: RunScope, chat: ChatScope) -> AgentContext:
            return build_context(run, None, chat)

        self.card_waiter = CardWaiter(self.chat_store, jobs)
        self.execute_action = ExecuteActionFlow(
            self.approvals, store, GmailActions(get_gmail_service, has_scope),
            on_settled=self.card_waiter.on_action_settled,
        )
        self.process_email = ProcessEmailFlow(
            store=store,
            trail=self.trail,
            executor=self.executor,
            loop=AgentLoop(self.executor, self.trail, gateway),
            rules=RuleEngine(sf),
            actions=self.actions,
            configs=DbAgentConfigs(sf),
            kb=kb,
            context_factory=context_factory,
        )
        self.chat_turn = ChatTurnFlow(
            store=self.chat_store,
            trail=self.trail,
            loop=AgentLoop(self.executor, self.trail, chat_gateway),
            configs=DbAgentConfigs(sf),
            kb=kb,
            approvals=self.approvals,
            context_factory=chat_context,
            structured=chat_gateway,
        )
