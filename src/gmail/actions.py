"""Executes approved actions on Gmail.

Deliberately has no permanent-delete path: the strongest action is moving
to TRASH (recoverable for 30 days). `block_sender` creates a filter and
needs the optional `gmail.settings.basic` scope.
"""
from collections.abc import Callable
from email.utils import parseaddr

from googleapiclient.discovery import Resource

from src.agent.types import ActionKind
from src.db.rows import EmailRow
from src.gmail.client import SETTINGS_SCOPE

USER_ID = "me"


class MissingScope(Exception):
    """The token lacks a scope the action needs; retrying will not help."""


class GmailActions:
    def __init__(
        self, service_factory: Callable[[], Resource], has_scope: Callable[[str], bool]
    ) -> None:
        self._service = service_factory
        self._has_scope = has_scope

    def _modify(self, email_id: str, body: dict[str, list[str]]) -> None:
        self._service().users().messages().modify(
            userId=USER_ID, id=email_id, body=body
        ).execute(num_retries=5)

    def execute(self, kind: ActionKind, email: EmailRow) -> None:
        match kind:
            case "mark_read":
                self._modify(email.id, {"removeLabelIds": ["UNREAD"]})
            case "star":
                self._modify(email.id, {"addLabelIds": ["STARRED"]})
            case "archive":
                self._modify(email.id, {"removeLabelIds": ["INBOX"]})
            case "trash":
                self._service().users().messages().trash(
                    userId=USER_ID, id=email.id
                ).execute(num_retries=5)
            case "block_sender":
                self._block(email)

    def _block(self, email: EmailRow) -> None:
        if not self._has_scope(SETTINGS_SCOPE):
            raise MissingScope(
                "bloquear remetente exige o escopo gmail.settings.basic: "
                "reautorize com python -m src.gmail.authorize e recopie o token.json"
            )
        address = parseaddr(email.sender or "")[1].lower()
        if "@" not in address:
            raise ValueError(f"remetente sem endereço: {email.sender!r}")
        self._service().users().settings().filters().create(
            userId=USER_ID,
            body={
                "criteria": {"from": address},
                "action": {"addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]},
            },
        ).execute(num_retries=5)
