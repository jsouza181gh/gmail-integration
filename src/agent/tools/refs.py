"""Recording which emails a chat answer referenced (see ChatStore.add_refs)."""
from collections.abc import Iterable

from src.agent.ports import RefSink
from src.agent.types import EmailRef


def remember(refs: RefSink | None, message_id: int | None, items: Iterable[EmailRef]) -> None:
    """No-op outside a chat turn."""
    if refs is None or message_id is None:
        return
    refs.add_refs(message_id, list(items))
