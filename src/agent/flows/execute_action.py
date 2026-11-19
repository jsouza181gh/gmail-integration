"""execute_action: runs one approved action on Gmail.

Not approved (pending, rejected, already executed) -> nothing happens.
A missing OAuth scope fails the action for good; other errors propagate so
the queue retries, and a dead job marks the action failed (worker hook).
"""
from typing import Protocol

from src.agent.approvals import ApprovalService
from src.agent.types import ActionKind, as_action_kind
from src.db.rows import EmailRow
from src.db.store import Store
from src.gmail.actions import MissingScope
from src.jobs.types import ClaimedJob, ExecuteActionJob
from src.utils.console import log


class ActionExecutor(Protocol):
    def execute(self, kind: ActionKind, email: EmailRow) -> None: ...


class ExecuteActionFlow:
    def __init__(self, approvals: ApprovalService, store: Store, gmail: ActionExecutor) -> None:
        self.approvals = approvals
        self.store = store
        self.gmail = gmail

    def handle(self, job: ClaimedJob) -> None:
        payload = job.payload
        if not isinstance(payload, ExecuteActionJob):
            raise TypeError(f"job {job.id} não é execute_action")
        action_id = payload.action_id
        found = self.approvals.approved(action_id)
        if found is None:
            log(f"ação #{action_id} não está aprovada; nada a executar")
            return
        kind_name, email_id = found
        kind = as_action_kind(kind_name)
        email = self.store.get_email(email_id)
        if kind is None or email is None:
            self.approvals.mark_failed(action_id, f"ação inválida ({kind_name}) ou e-mail ausente")
            return
        try:
            self.gmail.execute(kind, email)
        except MissingScope as exc:
            self.approvals.mark_failed(action_id, str(exc))
            log(f"ação #{action_id} falhou: {exc}")
            return
        self.approvals.mark_executed(action_id)
        log(f"ação #{action_id} executada: {kind} em {email_id}")
