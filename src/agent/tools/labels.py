"""apply_labels: the agent's own IA/* labels (automatic by policy)."""
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from src.agent.context import Deps
from src.agent.ports import Labeler
from src.agent.state import LabelsApplied, StateUpdate
from src.agent.types import PolicyDecision
from src.classification.taxonomy import Classification
from src.llm.types import Schema

PREFIX = "IA/"
RELEVANT = "IA/Relevante"


def label_names(c: Classification) -> tuple[str, ...]:
    """`IA/<categoria>` (a '/' inside the name would nest labels) + relevance."""
    names = [PREFIX + c.category.replace("/", "-")]
    if c.is_relevant:
        names.append(RELEVANT)
    return tuple(names)


class LabelCtx(Deps):
    labels: Labeler
    email_id: str
    policy: PolicyDecision


class LabelState(Schema):
    classification: Classification | None


@tool(response_format="content_and_artifact")
def apply_labels(runtime: ToolRuntime[LabelCtx, LabelState]) -> tuple[str, StateUpdate | None]:
    """Apply the agent's IA/* labels for the classification of the email in focus."""
    c = runtime.state.classification
    if c is None:
        raise ValueError("o e-mail ainda não foi classificado")
    ctx = runtime.context
    if ctx.policy != "auto":
        return f"labels não aplicados: política '{ctx.policy}'", None
    applied = ctx.labels.apply(ctx.email_id, label_names(c))
    return f"labels aplicados: {', '.join(applied)}", LabelsApplied(
        email_id=ctx.email_id, labels=applied
    )
