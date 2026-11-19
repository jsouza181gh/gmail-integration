"""classify_email: the classifier as a tool (LLM via runtime.context)."""
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from src.agent.context import Deps
from src.agent.ports import TaxonomySource
from src.agent.state import Classified, StateUpdate
from src.classification.classifier import (
    PREFERENCE_SCOPE,
    BatchResult,
    EmailIn,
    batch_schema,
    build_classify_prompt,
    to_classifications,
)
from src.db.rows import EmailRow
from src.llm.types import CallContext, Schema, StructuredGenerator


class ClassifyCtx(Deps):
    llm: StructuredGenerator
    taxonomy: TaxonomySource
    call: CallContext


class ClassifyState(Schema):
    email: EmailRow
    body: str | None


@tool(response_format="content_and_artifact")
def classify_email(
    runtime: ToolRuntime[ClassifyCtx, ClassifyState],
) -> tuple[str, StateUpdate | None]:
    """Classify the email in focus into the active taxonomy: category, reason,
    relevance, deadline, has_document and ephemeral."""
    ctx, state = runtime.context, runtime.state
    taxonomy = ctx.taxonomy.active_taxonomy()
    if taxonomy is None:
        raise RuntimeError("nenhuma taxonomia ativa")
    prompt = build_classify_prompt(
        taxonomy,
        ctx.taxonomy.active_preferences(PREFERENCE_SCOPE),
        [EmailIn.of(state.email, body=state.body)],
        ctx.call,
    )
    wanted = {state.email.id}

    def check(batch: BatchResult) -> str | None:
        if to_classifications(batch.items, wanted, taxonomy):
            return None
        got = [i.category for i in batch.items]
        return f"nenhuma categoria válida para {state.email.id} (veio {got})"

    result = ctx.llm.generate_structured(prompt, batch_schema(taxonomy), check)
    found = to_classifications(result.parsed.items, wanted, taxonomy)
    c = found.get(state.email.id)
    if c is None:
        raise ValueError("o modelo não devolveu uma classificação válida para este e-mail")
    flags = [
        f"relevante: {'sim' if c.is_relevant else 'não'}",
        f"prazo: {c.deadline.isoformat() if c.deadline else 'nenhum'}",
        f"documento: {'sim' if c.has_document else 'não'}",
        f"efêmero: {'sim' if c.ephemeral else 'não'}",
    ]
    content = f"categoria {c.category} ({'; '.join(flags)}): {c.reason}"
    return content, Classified(email_id=state.email.id, classification=c)
