"""Typed job payloads; one model per kind, discriminated by `kind`."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from src.llm.types import Schema

JobKind = Literal[
    "chat_turn", "process_email", "execute_action", "index_email", "index_attachment",
    "backfill_page",
]
JobStatus = Literal["queued", "running", "done", "dead"]

# Higher runs first. Spec order: chat > new email > approved action > backfill.
# Indexing new mail outranks the attachments it discovers, and both outrank
# the history backfill. Chat also has its own worker lane.
PRIORITY: dict[JobKind, int] = {
    "chat_turn": 50,
    "process_email": 30,
    "execute_action": 20,
    "index_email": 10,
    "index_attachment": 5,
    "backfill_page": 0,
}


class ChatTurnJob(Schema):
    kind: Literal["chat_turn"] = "chat_turn"
    message_id: int  # the assistant message to produce


class ProcessEmailJob(Schema):
    kind: Literal["process_email"] = "process_email"
    email_id: str


class ExecuteActionJob(Schema):
    kind: Literal["execute_action"] = "execute_action"
    action_id: int


class IndexEmailJob(Schema):
    kind: Literal["index_email"] = "index_email"
    email_id: str


class IndexAttachmentJob(Schema):
    kind: Literal["index_attachment"] = "index_attachment"
    attachment_id: int


class BackfillPageJob(Schema):
    kind: Literal["backfill_page"] = "backfill_page"
    run_id: int
    page_no: int


type AnyJob = (
    ChatTurnJob | ProcessEmailJob | ExecuteActionJob | IndexEmailJob | IndexAttachmentJob
    | BackfillPageJob
)
JobPayload = Annotated[
    ChatTurnJob | ProcessEmailJob | ExecuteActionJob | IndexEmailJob | IndexAttachmentJob
    | BackfillPageJob,
    Field(discriminator="kind"),
]


def idempotency_key(payload: AnyJob) -> str:
    match payload:
        case ChatTurnJob(message_id=m):
            return f"chat_turn:{m}"
        case ProcessEmailJob(email_id=e) | IndexEmailJob(email_id=e):
            return f"{payload.kind}:{e}"
        case ExecuteActionJob(action_id=a):
            return f"execute_action:{a}"
        case IndexAttachmentJob(attachment_id=a):
            return f"index_attachment:{a}"
        case BackfillPageJob(run_id=r, page_no=n):
            return f"backfill_page:{r}:{n}"


class ClaimedJob(Schema):
    id: int
    attempts: int
    payload: JobPayload


class JobCounts(Schema):
    queued: int = 0
    running: int = 0
    done: int = 0
    dead: int = 0


class DeadJob(Schema):
    id: int
    kind: str
    attempts: int
    last_error: str | None
    finished_at: datetime | None
