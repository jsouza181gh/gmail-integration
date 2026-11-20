from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from src.agent.approvals import ApprovalService
from src.agent.config import DbAgentConfigs
from src.agent.context import AgentContext, ChatScope, RunScope
from src.agent.executor import ToolExecutor
from src.agent.flows.chat_turn import ChatTurnFlow
from src.agent.kb import DbKb
from src.agent.loop import AgentLoop
from src.agent.rules import RuleCondition
from src.agent.tools import build_registry
from src.agent.trail import Trail
from src.agent.types import MailHit, RuleDraft
from src.chat.audio import parse_wav
from src.chat.store import ChatStore
from src.db.models import AgentConfig, KbEntry
from src.db.rows import RuleRow
from src.jobs.store import JobStore
from src.jobs.types import ChatTurnJob, ClaimedJob
from src.llm.errors import LLMExhaustedError
from src.llm.types import (
    AssistantTurn,
    CallContext,
    Check,
    Prompt,
    StructuredResult,
    TokenUsage,
    ToolCallRequest,
)
from tests.agent_fakes import FakeToolCaller, make_ctx, turn
from tests.chat_fakes import wav


class FakeTranscriber:
    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[Prompt] = []

    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T], check: Check[T] | None = None
    ) -> StructuredResult[T]:
        self.prompts.append(prompt)
        parsed = schema.model_validate({"text": self.text})
        return StructuredResult[T](parsed=parsed, model="m1", usage=TokenUsage())


class NoHits:
    def search(self, query: str, k: int, call: CallContext) -> tuple[MailHit, ...]:
        return ()


class EmptyRules:
    def existing(self, cond: RuleCondition, limit: int) -> tuple[list[str], int]:
        return [], 0

    def list(self) -> tuple[RuleRow, ...]:
        return ()

    def get(self, rule_id: int) -> RuleRow | None:
        return None

    def known_senders(self, address: str) -> tuple[int, tuple[str, ...]]:
        return 1, ()


class SpyStore(ChatStore):
    def __init__(self, sf: sessionmaker[Session]) -> None:
        super().__init__(sf)
        self.labels: list[str] = []

    def progress(self, assistant_id: int, label: str) -> None:
        self.labels.append(label)
        super().progress(assistant_id, label)


class World:
    def __init__(
        self,
        sf: sessionmaker[Session],
        turns: Sequence[AssistantTurn | Exception],
        transcript: str = "",
    ) -> None:
        with sf.begin() as s:
            s.add(AgentConfig(id=1, version=1, active=True, decide_prompt="p",
                              chat_prompt="Você é o assistente do Gmail.", max_turns=3,
                              created_at=datetime.now(UTC)))
            s.add(KbEntry(kind="preference", scope="decision", text="vagas backend",
                          source="ui", active=True, created_at=datetime.now(UTC)))
        self.sf = sf
        self.store = SpyStore(sf)
        self.trail = Trail(sf)
        self.caller = FakeToolCaller(turns)
        self.transcriber = FakeTranscriber(transcript)
        self.conv = self.store.create_conversation().id

        def context(run: RunScope, chat: ChatScope) -> AgentContext:
            return make_ctx(run_id=run.run_id, trigger="chat", email_id=None,
                            caller=self.caller,
                            extra={"chat": chat, "retriever": NoHits(), "rules": EmptyRules(),
                                   "cards": self.store})

        self.flow = ChatTurnFlow(
            store=self.store,
            trail=self.trail,
            loop=AgentLoop(ToolExecutor(build_registry(), self.trail), self.trail, self.caller),
            configs=DbAgentConfigs(sf),
            kb=DbKb(sf),
            approvals=ApprovalService(sf, JobStore(sf)),
            context_factory=context,
            structured=self.transcriber,
            clock=lambda: datetime(2026, 11, 19, 12, tzinfo=UTC),
        )

    def handle(self, assistant_id: int) -> None:
        self.flow.handle(ClaimedJob(id=1, attempts=1, payload=ChatTurnJob(message_id=assistant_id)))


def _call(name: str, cid: str, **args: str) -> ToolCallRequest:
    return ToolCallRequest(id=cid, name=name, args=dict(args))


def test_text_turn_answers_and_records_run(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [turn("Achei 2 boletos.")])
    t = w.store.start_turn(w.conv, text="quais boletos tenho?")
    w.handle(t.assistant.id)
    done = w.store.turn(t.assistant.id).assistant
    assert (done.status, done.text, done.progress) == ("done", "Achei 2 boletos.", None)
    assert done.run_id is not None
    run = w.trail.run(done.run_id)
    assert (run.trigger, run.status, run.email_id) == ("chat", "done", None)
    prompt = w.caller.prompts[0]
    assert prompt.purpose == "chat" and prompt.attempts == 3
    assert prompt.messages[-1].content == "quais boletos tenho?"


def test_audio_turn_transcribes_first(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [turn("Ok.")], transcript=" tudo da loja pra lixeira ")
    data = wav(1.5)
    t = w.store.start_turn(w.conv, audio=(data, parse_wav(data)))
    w.handle(t.assistant.id)
    user = w.store.turn(t.assistant.id).user
    assert (user.text, user.status) == ("tudo da loja pra lixeira", "done")
    (tp,) = w.transcriber.prompts
    assert tp.purpose == "transcribe" and tp.attempts == 3
    part = tp.messages[-1].parts[0]
    assert part.mime_type == "audio/wav" and part.data == data
    assert w.caller.prompts[0].messages[-1].content == "tudo da loja pra lixeira"
    assert "ouvindo o áudio…" in w.store.labels


def test_empty_transcript_fails_turn(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [], transcript="   ")
    data = wav(1)
    t = w.store.start_turn(w.conv, audio=(data, parse_wav(data)))
    w.handle(t.assistant.id)
    turn_view = w.store.turn(t.assistant.id)
    assert turn_view.assistant.status == "failed"
    assert "não entendi o áudio" in (turn_view.assistant.error or "").lower()
    assert turn_view.user.status == "done" and w.caller.prompts == []


def test_progress_follows_tools(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [turn("", _call("search_mail", "c1", query="light")),
                                turn("Nada.")])
    t = w.store.start_turn(w.conv, text="light")
    w.handle(t.assistant.id)
    assert "buscando nos e-mails…" in w.store.labels


def test_truncated_turn_message(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [turn("", _call("list_rules", f"c{i}")) for i in range(8)])
    t = w.store.start_turn(w.conv, text="regras")
    w.handle(t.assistant.id)
    done = w.store.turn(t.assistant.id).assistant
    assert done.status == "done" and done.text.startswith("Parei antes de terminar")


def test_llm_exhausted_marks_failed_without_raising(
    session_factory: sessionmaker[Session],
) -> None:
    w = World(session_factory, [LLMExhaustedError("503", kinds=("transient",))])
    t = w.store.start_turn(w.conv, text="oi")
    w.handle(t.assistant.id)
    failed = w.store.turn(t.assistant.id).assistant
    assert failed.status == "failed" and "sobrecarregado" in (failed.error or "")
    assert failed.run_id is not None and w.trail.run(failed.run_id).status == "failed"


def test_handle_is_idempotent_for_finished_message(
    session_factory: sessionmaker[Session],
) -> None:
    w = World(session_factory, [turn("Oi!")])
    t = w.store.start_turn(w.conv, text="oi")
    w.handle(t.assistant.id)
    w.handle(t.assistant.id)  # no turns left: a second run would fail the message
    assert w.store.turn(t.assistant.id).assistant.status == "done"
    assert len(w.caller.prompts) == 1


def test_prompt_fences_and_includes_history_cards(
    session_factory: sessionmaker[Session],
) -> None:
    w = World(session_factory, [turn("Entendi assim — confere?"), turn("Feito.")])
    first = w.store.start_turn(w.conv, text="lixo da loja")
    w.handle(first.assistant.id)
    w.store.add_card(first.assistant.id, "chat", RuleDraft(
        name="lixo", action="trash", sender_domain="loja.com", affected=1))
    second = w.store.start_turn(w.conv, text="confirmei")
    w.handle(second.assistant.id)
    msgs = w.caller.prompts[1].messages
    system = msgs[0].content
    assert system.startswith("Você é o assistente do Gmail.")
    assert "<email_data>" in system and "vagas backend" in system
    assert "2026-11-19" in system and "pendentes de aprovação: 0" in system
    assert [m.role for m in msgs] == ["system", "human", "ai", "human"]
    assert "[cartão #1 regra 'lixo'" in msgs[2].content


def test_missing_config_fails_turn(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [])
    with session_factory.begin() as s:
        s.get_one(AgentConfig, 1).active = False
    t = w.store.start_turn(w.conv, text="oi")
    w.handle(t.assistant.id)
    assert w.store.turn(t.assistant.id).assistant.status == "failed"


@pytest.fixture(autouse=True)
def _quiet_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.agent.flows.chat_turn.log", lambda msg: None)


def test_chat_turn_quota_reason() -> None:
    from src.agent.flows.chat_turn import _reason
    from src.llm.errors import LLMExhaustedError

    assert _reason(LLMExhaustedError("x", kinds=("quota",))) == (
        "A cota de hoje acabou em todos os modelos do chat; volta às 05h."
    )
