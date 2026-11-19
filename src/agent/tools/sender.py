"""sender_history: what our records say about the sender of the email in focus."""
from email.utils import parseaddr

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from src.agent.context import Deps
from src.agent.ports import SenderHistorySource
from src.agent.state import SenderLoaded, StateUpdate
from src.llm.types import Schema


class SenderCtx(Deps):
    history: SenderHistorySource


class SenderState(Schema):
    email_id: str
    sender: str | None


@tool(response_format="content_and_artifact")
def sender_history(
    runtime: ToolRuntime[SenderCtx, SenderState],
) -> tuple[str, StateUpdate | None]:
    """How many emails we have from this sender, their categories, and how the
    user answered past suggestions for them (approved, rejected)."""
    address = parseaddr(runtime.state.sender or "")[1].lower()
    if not address:
        raise ValueError("remetente sem endereço reconhecível")
    stats = runtime.context.history.stats(address)
    return stats.model_dump_json(exclude_none=True), SenderLoaded(
        email_id=runtime.state.email_id, stats=stats
    )
