"""propose_action: the only way the agent acts on an email.

The tool never touches Gmail. It hands the proposal to the action sink,
which applies the policy (suggest / auto / deny) and tells the model what
happened.
"""
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from src.agent.context import Deps, RunScope
from src.agent.ports import ActionSink
from src.agent.state import ActionProposed, StateUpdate
from src.agent.types import ActionKind, ProposalRef
from src.llm.types import Schema


class ActCtx(Deps):
    actions: ActionSink
    email_id: str
    run: RunScope


class ActState(Schema):
    proposals: tuple[ProposalRef, ...]


def _describe(ref: ProposalRef) -> str:
    match ref.outcome:
        case "suggested":
            return f"sugerido #{ref.action_id} (aguarda aprovação do usuário)"
        case "auto":
            return f"aprovado automaticamente pela política #{ref.action_id}; será executado"
        case "denied":
            return "negado pela política; não sugira de novo neste e-mail"
        case "duplicate":
            return f"já sugerido antes (#{ref.action_id}); nada novo foi criado"


@tool(response_format="content_and_artifact", parse_docstring=True)
def propose_action(
    kind: ActionKind, reason: str, runtime: ToolRuntime[ActCtx, ActState]
) -> tuple[str, StateUpdate | None]:
    """Propose one action on the email in focus. Sensitive actions wait for the
    user's approval; nothing is executed by this call itself.

    Args:
        kind: mark_read, star, archive, trash or block_sender (creates a
            Gmail filter sending the sender's future mail to trash).
        reason: Short reason in pt-BR, shown to the user on the approval card.
    """
    ctx = runtime.context
    for ref in runtime.state.proposals:
        if ref.kind == kind and ref.email_id == ctx.email_id:
            return f"{kind}: já proposto nesta execução ({_describe(ref)})", None
    ref = ctx.actions.propose(
        ctx.email_id, kind, reason, run_id=ctx.run.run_id, trigger=ctx.run.trigger
    )
    return f"{kind}: {_describe(ref)}", ActionProposed(ref=ref)
