"""process_email: what happens to every new email.

Planned part (no model decision needed, same executor as model calls):
classify -> rules -> IA labels -> sender history. Then, unless a rule
already decided, a short agentic loop proposes actions. Classification is
the only step whose failure fails the run (the job retries); the others are
recorded as step errors and the flow goes on.
"""
from collections.abc import Callable

from src.agent.config import DbAgentConfigs
from src.agent.context import AgentContext, RunScope
from src.agent.executor import ToolExecutor
from src.agent.kb import DbKb
from src.agent.loop import AgentLoop
from src.agent.ports import ActionSink
from src.agent.prompts import DECIDE_SCOPES, build_decide_prompt
from src.agent.rules import RuleEngine
from src.agent.state import AgentState, EmailFacts, reduce
from src.agent.trail import RunStatus, Trail
from src.db.rows import AgentConfigRow
from src.db.store import Store
from src.jobs.types import ClaimedJob, ProcessEmailJob
from src.llm.types import ToolCallRequest
from src.utils.console import log

ContextFactory = Callable[[RunScope, str], AgentContext]


class ProcessEmailFlow:
    def __init__(
        self,
        *,
        store: Store,
        trail: Trail,
        executor: ToolExecutor,
        loop: AgentLoop,
        rules: RuleEngine,
        actions: ActionSink,
        configs: DbAgentConfigs,
        kb: DbKb,
        context_factory: ContextFactory,
    ) -> None:
        self.store = store
        self.trail = trail
        self.executor = executor
        self.loop = loop
        self.rules = rules
        self.actions = actions
        self.configs = configs
        self.kb = kb
        self.context_factory = context_factory

    def _planned(
        self, name: str, ctx: AgentContext, state: AgentState
    ) -> tuple[AgentState, bool, str]:
        call = ToolCallRequest(id=f"planned-{name}", name=name, args={})
        result = self.executor.execute(call, ctx, state, source="planned")
        if result.update is not None:
            state = reduce(state, result.update)
        return state, result.ok, result.message.content

    def handle(self, job: ClaimedJob) -> None:
        payload = job.payload
        if not isinstance(payload, ProcessEmailJob):
            raise TypeError(f"job {job.id} não é process_email")
        email = self.store.get_email(payload.email_id)
        if email is None:
            log(f"e-mail {payload.email_id} não está no banco; nada a fazer")
            return
        config = self.configs.active()
        if config is None:
            raise RuntimeError("nenhuma agent_config ativa; rode as migrações")
        if self.trail.has_done_run(email.id, config.id):
            return

        run_id = self.trail.start_run("webhook", email.id, config.id)
        try:
            state = AgentState(emails={email.id: EmailFacts(email=email)})
            status, summary = self._run(run_id, config, email.id, state)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.trail.finish_run(run_id, "failed", summary=None, error=error)
            raise
        self.trail.finish_run(run_id, status, summary=summary, error=None)

    def _run(
        self, run_id: int, config: AgentConfigRow, email_id: str, state: AgentState
    ) -> tuple[RunStatus, str]:
        ctx = self.context_factory(
            RunScope(run_id=run_id, trigger="webhook", config_id=config.id), email_id
        )

        state, ok, message = self._planned("classify_email", ctx, state)
        classification = state.emails[email_id].classification
        if not ok or classification is None:
            raise RuntimeError(f"classificação falhou: {message}")
        self.store.save_classifications({email_id: classification})

        email = state.emails[email_id].email
        matches = self.rules.match(email, classification)
        outcomes: list[str] = []
        for m in matches:
            step = self.trail.open_step(run_id, "rule", m.rule.name[:64], "flow")
            ref = self.actions.propose(
                email_id,
                m.action,
                f"regra '{m.rule.name}'",
                run_id=run_id,
                trigger="webhook",
                rule=m.rule,
            )
            self.rules.record_fired(m.rule.rule_id)
            self.trail.close_step(step, "ok", f"{m.action} -> {ref.outcome} #{ref.action_id}")
            outcomes.append(f"{m.action} ({ref.outcome})")

        state, _, _ = self._planned("apply_labels", ctx, state)

        if matches:
            return "done", "regras aplicadas: " + ", ".join(outcomes)

        state, _, _ = self._planned("sender_history", ctx, state)
        kb = self.kb.entries(DECIDE_SCOPES)
        prompt = build_decide_prompt(config, state.emails[email_id], kb)
        result = self.loop.run(ctx, state, prompt, max_turns=config.max_turns)
        return result.status, result.summary
