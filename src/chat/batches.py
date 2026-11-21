"""Texts of an actions batch: progress while it runs, the event once it ran."""
import re
from math import ceil


BATCH_SIZE = 50  # propose_email_actions' BATCH_MAX
VERB = {
    "trash": "apagando", "archive": "arquivando", "mark_read": "marcando como lidos",
    "star": "destacando", "block_sender": "bloqueando",
}


def progress_text(kind: str, done: int, total: int) -> str:
    return f"{VERB.get(kind, 'executando')}… {done}/{total}"


DONE = {
    "trash": "apagado(s)", "archive": "arquivado(s)", "mark_read": "marcado(s) como lido(s)",
    "star": "destacado(s)", "block_sender": "bloqueado(s)",
}
EVENT = "[evento] "
DONE_EVENT = re.compile(r"^\[evento\] (Lote \d+ de \d+ concluído \(cartão #\d+\)|Cartão #\d+ concluído):")


def batch_done_event(
    card_id: int, kind: str, executed: int, failed: int, error: str | None,
    batch_query: str | None, batch_total: int | None, batch_no: int | None,
) -> str:
    """What the agent hears once every approved action of a card has run; the
    same sentence (without the marker) opens the agent's answer."""
    if batch_query and batch_total is not None and batch_no is not None:
        batches = max(batch_no, ceil(batch_total / BATCH_SIZE))
        head = f"Lote {batch_no} de {batches} concluído (cartão #{card_id})"
        left = max(0, batch_total - batch_no * BATCH_SIZE)
        tail = f" Restam ~{left} na busca '{batch_query}'." if left else " Era o último lote."
    else:
        head, tail = f"Cartão #{card_id} concluído", ""
    text = f"{EVENT}{head}: {executed} e-mail(s) {DONE.get(kind, 'executado(s)')}"
    if failed:
        text += f", {failed} falhou" + (f" ({error})" if error else "")
    return text + "." + tail


def done_summary(event_text: str) -> str | None:
    """The user-facing result of a finished card, from its [evento]; None for
    any other message."""
    if not DONE_EVENT.match(event_text):
        return None
    return event_text.removeprefix(EVENT)
