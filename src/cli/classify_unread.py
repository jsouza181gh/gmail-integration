"""Resumable unread-email classifier: fetch pages, classify, print report."""
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from pydantic import BaseModel

from src.classification.classifier import (
    GIVE_UP_CATEGORY,
    Abort,
    Classifier,
    classify_pending,
)
from src.cli.account import cli_account
from src.db.rows import EmailRow, RunRow
from src.db.store import Store
from src.gmail.client import get_gmail_service
from src.gmail.messages import USER_ID, message_summary
from src.llm.config import build_gateway
from src.llm.recorder import DbCallRecorder
from src.utils.console import log

DEFAULT_QUERY = "is:unread in:inbox"
PAGE_SIZE = 50


class _MessageRef(BaseModel):
    id: str


class _ListResponse(BaseModel):
    """The slice of a `messages.list` response we read."""

    messages: list[_MessageRef] = []
    nextPageToken: str | None = None


def fetch_page(store: Store, service: Resource, run: RunRow) -> None:
    """Fetch one page of the run's query and persist it with the new cursor."""
    params: dict[str, str | int] = {
        "userId": USER_ID,
        "q": run.query,
        "maxResults": PAGE_SIZE,
    }
    if run.page_token:
        params["pageToken"] = run.page_token

    try:
        raw = service.users().messages().list(**params).execute(num_retries=5)
    except HttpError as e:
        if run.page_token and e.resp.status == 400:
            log("page token inválido/expirado; recomeçando a listagem (cache evita refetch)")
            store.reset_cursor(run.id)
            return
        raise

    result = _ListResponse.model_validate(raw)
    ids = [m.id for m in result.messages]
    known = store.known_ids(ids)
    new_emails = [message_summary(service, i) for i in ids if i not in known]
    store.save_page(run.id, ids, new_emails, result.nextPageToken)
    log(
        f"página {run.pages + 1}: {len(ids)} emails "
        f"({len(new_emails)} novos, {len(known)} em cache)"
    )


# --- report -----------------------------------------------------------------

def print_report(store: Store, run_id: int) -> None:
    rows = store.report_rows(run_id)
    groups: dict[str, list[EmailRow]] = {}
    for r in rows:
        groups.setdefault(r.category or GIVE_UP_CATEGORY, []).append(r)

    taxonomy = store.active_taxonomy()
    order = (taxonomy.names if taxonomy else []) + [GIVE_UP_CATEGORY]
    order += sorted(c for c in groups if c not in order)  # older taxonomy versions

    print(f"\nTotal de e-mails: {len(rows)}\n")
    for cat in order:
        if cat in groups:
            print(f"- {cat}: {len(groups[cat])}")

    for cat in order:
        items = groups.get(cat)
        if not items:
            continue
        print(f"\n## {cat} ({len(items)})")
        for r in items:
            flags = " ".join(
                label
                for label, on in (
                    ("[relevante]", r.is_relevant),
                    (f"[prazo {r.deadline}]", r.deadline is not None),
                    ("[documento]", r.has_document),
                    ("[efêmero]", r.ephemeral),
                )
                if on
            )
            print(f"- {r.sender} — {r.subject} — {r.date} — {r.reason} {flags}".rstrip())


# --- entry point ------------------------------------------------------------

def build_classifier(store: Store) -> Classifier:
    return Classifier(store, build_gateway(DbCallRecorder(store.sf)))


def run(
    query: str = DEFAULT_QUERY, reset: bool = False, max_pages: int | None = None,
    account: str | None = None,
) -> int:
    store = Store()
    try:
        with cli_account(store.sf, account):
            current = store.get_or_create_run(query, force_new=reset)
            run_id = current.id
            log(
                f"run #{run_id} ({'retomando' if current.pages else 'nova'}): "
                f"{current.pages} páginas já buscadas, "
                f"{store.count_pending(run_id)} pendentes"
            )

            service = get_gmail_service()
            classifier = build_classifier(store)
            pages_this_session = 0

            classify_pending(store, classifier, run_id)
            while True:
                current = store.get_run(run_id)
                if current.fetch_done:
                    break
                if max_pages is not None and pages_this_session >= max_pages:
                    log(f"limite de {max_pages} páginas; rode de novo para continuar")
                    break
                fetch_page(store, service, current)
                pages_this_session += 1
                classify_pending(store, classifier, run_id)

            if store.get_run(run_id).fetch_done and not store.count_pending(run_id):
                print_report(store, run_id)
            return 0
    except Abort as exc:
        log(f"ABORTADO: {exc}")
        return 1
    except KeyboardInterrupt:
        log("interrompido; progresso salvo")
        return 130
    except Exception as exc:  # noqa: BLE001
        log(f"ERRO: {type(exc).__name__}: {exc}; progresso salvo, rode de novo")
        return 1
    finally:
        store.close()
