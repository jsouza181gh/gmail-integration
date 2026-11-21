"""Texts of an actions batch: progress while it runs, the event once it ran."""
from math import ceil


BATCH_SIZE = 50  # propose_email_actions' BATCH_MAX
VERB = {
    "trash": "apagando", "archive": "arquivando", "mark_read": "marcando como lidos",
    "star": "destacando", "block_sender": "bloqueando",
}


def progress_text(kind: str, done: int, total: int) -> str:
    return f"{VERB.get(kind, 'executando')}… {done}/{total}"


def batch_done_event(
    card_id: int, kind: str, executed: int, failed: int, error: str | None,
    batch_query: str | None, batch_total: int | None, batch_no: int | None,
) -> str:
    """What the agent hears once every approved action of a card has run."""
    text = f"[evento] Lote do cartão #{card_id} ({kind}) concluído: {executed} executada(s)"
    if failed:
        text += f", {failed} falhou" + (f" ({error})" if error else "")
    text += "."
    if batch_query and batch_total is not None and batch_no is not None:
        batches = max(batch_no, ceil(batch_total / BATCH_SIZE))
        left = max(0, batch_total - batch_no * BATCH_SIZE)
        text += f" Lote {batch_no} de {batches}"
        text += f"; restam ~{left} na busca '{batch_query}'." if left else "; era o último."
    return text
