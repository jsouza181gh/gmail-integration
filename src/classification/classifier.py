"""LLM email classification: batching, retries, model fallback.

One stateless LLM call per batch of pending emails; every step is persisted
through Store, so a crash resumes where it stopped.
"""
import json
import os
import random
import re
import time
from typing import Literal

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from src.utils.console import log

load_dotenv()

BATCH_SIZE = int(os.getenv("CLASSIFY_BATCH_SIZE", "25"))
MODELS = [
    m.strip()
    for m in os.getenv("GEMINI_MODELS", "gemini-3.5-flash-lite").split(",")
    if m.strip()
]
MIN_CALL_INTERVAL_S = float(os.getenv("MIN_CALL_INTERVAL_S", "4"))
MAX_CALL_ATTEMPTS = 8        # per batch, across models
MAX_EMAIL_ATTEMPTS = 4       # per email, across batches
MAX_CONSECUTIVE_FAILURES = 3  # failed batches in a row before aborting
BACKOFF_BASE_S = 3
BACKOFF_MAX_S = 120

Category = Literal[
    "Urgente",
    "Trabalho",
    "Financeiro",
    "Pessoal",
    "Notificações",
    "Newsletter/Promoções",
    "Outros",
]
CATEGORY_ORDER = list(Category.__args__) + ["Não classificado"]

SYSTEM_PROMPT = """You classify emails. For EVERY email received, return exactly
one item with its id, one category and a short reason (max 12 words, pt-BR).
Categories:
- Urgente: needs action or reply soon (deadlines, security alerts, payment
  problems, direct questions from real people, meeting changes).
- Trabalho: work-related, not urgent (projects, clients, team threads).
- Financeiro: invoices, receipts, bank, billing, subscription charges.
- Pessoal: friends, family, personal matters.
- Notificações: automated alerts from apps, services, social networks, job alerts.
- Newsletter/Promoções: marketing, newsletters, offers, mailing lists.
- Outros: anything that fits none of the above.
Never invent ids. Use only the ids you were given."""


class Item(BaseModel):
    id: str
    category: Category
    reason: str = Field(description="short reason, max 12 words, pt-BR")


class BatchResult(BaseModel):
    items: list[Item]


class Abort(Exception):
    """Stop the run; state is saved and a re-run resumes."""


# --- LLM classification -----------------------------------------------------

def _error_kind(exc):
    text = f"{type(exc).__name__} {exc}"
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "rate"
    if any(
        s in text
        for s in (
            "500", "502", "503", "504", "UNAVAILABLE", "INTERNAL",
            "DEADLINE", "imeout", "Connect", "Connection", "Remote",
        )
    ):
        return "transient"
    if any(s in text for s in ("ValidationError", "OutputParser", "JSONDecode")):
        return "bad_output"
    return "fatal"


def _retry_delay(exc):
    text = str(exc)
    for pattern in (
        r"retry in ([\d.]+)\s*s",
        r"retryDelay['\"]?:\s*['\"]([\d.]+)s",
    ):
        m = re.search(pattern, text, re.I)
        if m:
            return float(m.group(1))
    return None


def _sleep(seconds, why):
    seconds = max(1.0, seconds) + random.uniform(0, 2)
    log(f"{why}; aguardando {seconds:.0f}s")
    time.sleep(seconds)


class Classifier:
    def __init__(self):
        self.llms = [
            ChatGoogleGenerativeAI(model=m, max_retries=0, timeout=90)
            .with_structured_output(BatchResult)
            for m in MODELS
        ]
        self.last_call = 0.0

    def _pace(self):
        wait = MIN_CALL_INTERVAL_S - (time.monotonic() - self.last_call)
        if wait > 0:
            time.sleep(wait)

    def classify(self, rows):
        payload = json.dumps(
            [
                {
                    "id": r.id,
                    "from": r.sender,
                    "subject": r.subject,
                    "date": r.date,
                    "snippet": r.snippet,
                }
                for r in rows
            ],
            ensure_ascii=False,
        )
        messages = [("system", SYSTEM_PROMPT), ("human", payload)]
        wanted = {r.id for r in rows}
        last_exc = None

        for attempt in range(MAX_CALL_ATTEMPTS):
            idx = attempt % len(self.llms)
            self._pace()
            try:
                result = self.llms[idx].invoke(messages)
                self.last_call = time.monotonic()
                if result is None:
                    raise ValueError("ValidationError: empty structured output")
                return {
                    i.id: (i.category, i.reason.strip()[:200])
                    for i in result.items
                    if i.id in wanted
                }
            except Exception as exc:  # noqa: BLE001 - classified below
                self.last_call = time.monotonic()
                last_exc = exc
                kind = _error_kind(exc)
                model = MODELS[idx]
                if kind == "fatal":
                    raise Abort(f"erro não recuperável ({model}): {exc}") from exc
                if kind == "rate":
                    delay = _retry_delay(exc)
                    if delay is None:
                        delay = min(BACKOFF_BASE_S * 2 ** attempt, BACKOFF_MAX_S)
                    _sleep(delay + 1, f"429 em {model}")
                elif kind == "transient":
                    delay = min(BACKOFF_BASE_S * 2 ** attempt, BACKOFF_MAX_S)
                    _sleep(delay, f"{str(exc)[:60]!r} em {model}")
                else:  # bad_output: retry quickly
                    log(f"saída inválida de {model}: {str(exc)[:80]!r}; tentando de novo")

        raise RuntimeError(f"esgotou {MAX_CALL_ATTEMPTS} tentativas: {last_exc}")


def classify_pending(store, classifier, run_id):
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
        store.save_classifications(
            (eid, cat, reason) for eid, (cat, reason) in results.items()
        )
        missing = [i for i in ids if i not in results]
        if missing:
            store.bump_attempts(missing, "sem resposta do modelo")
            store.give_up(
                MAX_EMAIL_ATTEMPTS, "Não classificado", "modelo não respondeu"
            )
        log(
            f"lote: {len(results)}/{len(ids)} classificados; "
            f"{store.count_pending(run_id)} pendentes"
        )
