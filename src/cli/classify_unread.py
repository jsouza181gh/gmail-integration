"""Resumable unread-email classifier: fetch pages, classify, print report."""
from googleapiclient.errors import HttpError

from src.classification.classifier import (
    CATEGORY_ORDER,
    Abort,
    Classifier,
    classify_pending,
)
from src.db.store import Store
from src.gmail.client import get_gmail_service
from src.gmail.messages import USER_ID, message_summary
from src.utils.console import log

DEFAULT_QUERY = "is:unread in:inbox"
PAGE_SIZE = 50


def fetch_page(store, service, run):
    """Fetch one page of the run's query and persist it with the new cursor."""
    params = {
        "userId": USER_ID,
        "q": run.query,
        "maxResults": PAGE_SIZE,
    }
    if run.page_token:
        params["pageToken"] = run.page_token

    try:
        result = service.users().messages().list(**params).execute(num_retries=5)
    except HttpError as e:
        if run.page_token and e.resp.status == 400:
            log("page token inválido/expirado; recomeçando a listagem (cache evita refetch)")
            store.reset_cursor(run.id)
            return
        raise

    ids = [m["id"] for m in result.get("messages", [])]
    known = store.known_ids(ids)
    new_emails = [message_summary(service, i) for i in ids if i not in known]
    store.save_page(run.id, ids, new_emails, result.get("nextPageToken"))
    log(
        f"página {run.pages + 1}: {len(ids)} emails "
        f"({len(new_emails)} novos, {len(known)} em cache)"
    )


# --- report -----------------------------------------------------------------

def print_report(store, run_id):
    rows = store.report_rows(run_id)
    groups = {}
    for r in rows:
        groups.setdefault(r.category or "Não classificado", []).append(r)

    print(f"\nTotal de e-mails não lidos: {len(rows)}\n")
    for cat in CATEGORY_ORDER:
        if cat in groups:
            print(f"- {cat}: {len(groups[cat])}")

    for cat in CATEGORY_ORDER:
        items = groups.get(cat)
        if not items:
            continue
        print(f"\n## {cat} ({len(items)})")
        for r in items:
            print(
                f"- {r.sender} — {r.subject} — {r.date} — {r.reason}"
            )


# --- entry point ------------------------------------------------------------

def run(query=DEFAULT_QUERY, reset=False, max_pages=None):
    store = Store()
    try:
        current = store.get_or_create_run(query, force_new=reset)
        run_id = current.id
        log(
            f"run #{run_id} ({'retomando' if current.pages else 'nova'}): "
            f"{current.pages} páginas já buscadas, "
            f"{store.count_pending(run_id)} pendentes"
        )

        service = get_gmail_service()
        classifier = Classifier()
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
