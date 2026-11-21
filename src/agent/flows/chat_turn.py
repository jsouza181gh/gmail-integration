"""chat_turn: one answer of the agent in the panel's chat.

Transcribe the user's audio when there is one, then run the agent loop with
the chat tools over the conversation's recent history. Chat fails fast: any
error ends the turn `failed` with a pt-BR reason and the job completes (the
user retries from the panel); nothing is raised to the queue.
"""
from collections.abc import Callable
from datetime import UTC, datetime

from src.agent.approvals import ApprovalService
from src.agent.config import DbAgentConfigs
from src.agent.context import AgentContext, ChatScope, RunScope
from src.agent.kb import DbKb
from src.agent.loop import AgentLoop
from src.agent.prompts import (
    CHAT_MAX_TURNS,
    DECIDE_SCOPES,
    DEFAULT_TOOL_LABEL,
    TOOL_LABELS,
    Transcript,
    build_chat_prompt,
    build_transcribe_prompt,
)
from src.agent.state import AgentState
from src.agent.trail import RunStatus, Trail
from src.agent.types import ChatChannel
from src.chat.batches import done_summary
from src.chat.store import ChatStore
from src.db.rows import AgentConfigRow
from src.jobs.types import ChatTurnJob, ClaimedJob
from src.llm.errors import LLMExhaustedError, LLMFatalError
from src.llm.types import StructuredGenerator
from src.utils.console import log

ChatContextFactory = Callable[[RunScope, ChatScope], AgentContext]

NOT_UNDERSTOOD = "Não entendi o áudio; tente de novo."
OVERLOADED = "O Gemini está sobrecarregado agora. Tente de novo em instantes."
NO_QUOTA = "A cota de hoje acabou em todos os modelos do chat; volta às 05h."
TRUNCATED = "Parei antes de terminar. Pode reformular ou pedir de novo?"
SUMMARY_MAX = 4000


class _TurnFailed(Exception):
    """A reason already written for the user."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _reason(exc: BaseException) -> str:
    match exc:
        case _TurnFailed():
            return str(exc)
        case LLMExhaustedError() if exc.kinds == ("quota",):
            return NO_QUOTA
        case LLMExhaustedError():
            return OVERLOADED
        case LLMFatalError():
            return f"Erro do modelo: {str(exc)[:200]}"
        case _:
            return f"Erro interno: {type(exc).__name__}"


class ChatTurnFlow:
    def __init__(
        self,
        *,
        store: ChatStore,
        trail: Trail,
        loop: AgentLoop,
        configs: DbAgentConfigs,
        kb: DbKb,
        approvals: ApprovalService,
        context_factory: ChatContextFactory,
        structured: StructuredGenerator,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.store = store
        self.trail = trail
        self.loop = loop
        self.configs = configs
        self.kb = kb
        self.approvals = approvals
        self.context_factory = context_factory
        self.structured = structured
        self._now = clock

    def handle(self, job: ClaimedJob) -> None:
        payload = job.payload
        if not isinstance(payload, ChatTurnJob):
            raise TypeError(f"job {job.id} não é chat_turn")
        message_id = payload.message_id
        if not self.store.begin(message_id):
            return
        run_id: int | None = None
        try:
            config = self.configs.active()
            if config is None:
                raise _TurnFailed("Nenhuma configuração do agente está ativa.")
            run_id = self.trail.start_run("chat", None, config.id)
            self.store.attach_run(message_id, run_id)
            status, answer = self._answer(message_id, run_id, config)
        except Exception as exc:  # noqa: BLE001 - every failure ends the turn, never the job
            log(f"chat: turno {message_id} falhou: {type(exc).__name__}: {exc}")
            self.store.fail(message_id, _reason(exc))
            if run_id is not None:
                self.trail.finish_run(run_id, "failed", summary=None,
                                      error=f"{type(exc).__name__}: {exc}")
            return
        self.store.finish(message_id, answer)
        self.trail.finish_run(run_id, status, summary=answer[:SUMMARY_MAX], error=None)

    def _answer(
        self, message_id: int, run_id: int, config: AgentConfigRow
    ) -> tuple[RunStatus, str]:
        turn = self.store.turn(message_id)
        user = turn.user
        channel: ChatChannel = "audio" if user.input == "audio" else "chat"
        scope = ChatScope(
            conversation_id=turn.assistant.conversation_id, message_id=message_id,
            channel=channel,
        )
        ctx = self.context_factory(
            RunScope(run_id=run_id, trigger="chat", config_id=config.id), scope
        )
        text = user.text
        if user.input == "audio" and not text:
            text = self._transcribe(message_id, user.id, ctx, user.model)

        prompt = build_chat_prompt(
            config,
            self.store.history(scope.conversation_id, before_id=user.id),
            self.kb.entries(DECIDE_SCOPES),
            text,
            self._now().date(),
            len(self.approvals.pending()),
            user.model,
        )
        result = self.loop.run(
            ctx, AgentState(emails={}), prompt, max_turns=CHAT_MAX_TURNS,
            on_tool=lambda name: self.store.progress(
                message_id, TOOL_LABELS.get(name, DEFAULT_TOOL_LABEL)
            ),
        )
        if result.status == "truncated":
            return "truncated", TRUNCATED
        answer = result.summary or "Pronto."
        # a finished batch: the real numbers open the answer, whatever the model says
        if user.input == "event" and (summary := done_summary(user.text)) is not None:
            answer = f"{summary}\n\n{answer}"
        return "done", answer

    def _transcribe(
        self, message_id: int, user_id: int, ctx: AgentContext, model: str | None
    ) -> str:
        self.store.progress(message_id, "ouvindo o áudio…")
        audio = self.store.audio(user_id)
        if audio is None:
            raise _TurnFailed(NOT_UNDERSTOOD)
        mime, data = audio
        prompt = build_transcribe_prompt(mime, data, model).model_copy(
            update={"context": ctx.call()}
        )
        text = self.structured.generate_structured(prompt, Transcript).parsed.text.strip()
        if not text:
            raise _TurnFailed(NOT_UNDERSTOOD)
        self.store.set_transcript(user_id, text)
        return text

