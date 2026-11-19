"""LLM email classification over the gateway: batching and give-up.

One stateless LLM call per batch of pending emails; every step is persisted
through Store, so a crash resumes where it stopped. Retries, pacing and model
fallback live in the gateway. Taxonomy and preferences are re-read from the
database on every batch, so changes apply without a restart.
"""
import os
from collections.abc import Sequence
from datetime import date
from typing import Protocol

from pydantic import TypeAdapter

from src.classification.prompt import build_system_prompt
from src.classification.taxonomy import Classification
from src.db.rows import EmailRow
from src.db.store import Store
from src.llm.errors import LLMFatalError
from src.llm.types import (
    CallContext,
    ChatMessage,
    Prompt,
    Schema,
    StructuredGenerator,
)
from src.utils.console import log

BATCH_SIZE = int(os.getenv("CLASSIFY_BATCH_SIZE", "25"))
MAX_EMAIL_ATTEMPTS = 4       # per email, across batches
MAX_CONSECUTIVE_FAILURES = 3  # failed batches in a row before aborting
GIVE_UP_CATEGORY = "Não classificado"
PREFERENCE_SCOPE = "classification"
REASON_MAX = 200


class Item(Schema):
    """What the model returns per email (category validated afterwards)."""

    id: str
    category: str
    reason: str
    is_relevant: bool
    deadline: str | None
    has_document: bool
    ephemeral: bool


class BatchResult(Schema):
    items: list[Item]


class _EmailIn(Schema):
    id: str
    sender: str | None
    subject: str | None
    date: str | None
    snippet: str | None


_EMAILS_IN = TypeAdapter(list[_EmailIn])


__all__ = [
    "Abort", "BatchResult", "Classification", "Classifier", "EmailClassifier", "Item",
    "classify_pending",
]


class Abort(Exception):
    """Stop the run; state is saved and a re-run resumes."""


class EmailClassifier(Protocol):
    def classify(self, rows: Sequence[EmailRow]) -> dict[str, Classification]: ...


def _parse_deadline(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


class Classifier:
    def __init__(self, store: Store, gateway: StructuredGenerator) -> None:
        self.store = store
        self.gateway = gateway

    def classify(self, rows: Sequence[EmailRow]) -> dict[str, Classification]:
        taxonomy = self.store.active_taxonomy()
        if taxonomy is None:
            raise Abort("nenhuma taxonomia ativa; rode as migrações (alembic upgrade head)")
        preferences = self.store.active_preferences(PREFERENCE_SCOPE)
        payload = _EMAILS_IN.dump_json(
            [
                _EmailIn(id=r.id, sender=r.sender, subject=r.subject, date=r.date, snippet=r.snippet)
                for r in rows
            ]
        ).decode()
        prompt = Prompt(
            purpose="classify",
            messages=(
                ChatMessage(role="system", content=build_system_prompt(taxonomy, preferences)),
                ChatMessage(role="human", content=payload),
            ),
            context=CallContext(email_ids=tuple(r.id for r in rows)),
        )
        try:
            result = self.gateway.generate_structured(prompt, BatchResult)
        except LLMFatalError as exc:
            raise Abort(str(exc)) from exc

        wanted = {r.id for r in rows}
        valid = set(taxonomy.names)
        out: dict[str, Classification] = {}
        for i in result.parsed.items:
            if i.id not in wanted or i.category not in valid:
                continue
            out[i.id] = Classification(
                category=i.category,
                reason=i.reason.strip()[:REASON_MAX],
                is_relevant=i.is_relevant,
                deadline=_parse_deadline(i.deadline),
                has_document=i.has_document,
                ephemeral=i.ephemeral,
                category_set_id=taxonomy.set_id,
            )
        return out


def classify_pending(store: Store, classifier: EmailClassifier, run_id: int) -> None:
    failures = 0
    while True:
        rows = store.pending(run_id, BATCH_SIZE)
        if not rows:
            return
        ids = [r.id for r in rows]
        try:
            results = classifier.classify(rows)
        except Abort:
            raise
        except Exception as exc:  # noqa: BLE001
            store.bump_attempts(ids, exc)
            failures += 1
            log(f"lote falhou ({failures}/{MAX_CONSECUTIVE_FAILURES}): {exc}")
            if failures >= MAX_CONSECUTIVE_FAILURES:
                raise Abort(
                    "falhas seguidas; progresso salvo, rode de novo para retomar"
                ) from exc
            continue

        failures = 0
        store.save_classifications(results)
        missing = [i for i in ids if i not in results]
        if missing:
            store.bump_attempts(missing, "sem resposta válida do modelo")
            store.give_up(MAX_EMAIL_ATTEMPTS, GIVE_UP_CATEGORY, "modelo não respondeu")
        log(
            f"lote: {len(results)}/{len(ids)} classificados; "
            f"{store.count_pending(run_id)} pendentes"
        )
