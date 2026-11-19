"""Typed job payloads; one model per kind, discriminated by `kind`."""
from typing import Annotated, Literal

from pydantic import Field

from src.llm.types import Schema

JobKind = Literal["process_email", "execute_action"]
JobStatus = Literal["queued", "running", "done", "dead"]

# Higher runs first. Spec order: chat > new email > approved action > backfill
# (chat enters with 50 and backfill with 0 in later subprojects).
PRIORITY: dict[JobKind, int] = {"process_email": 30, "execute_action": 20}


class ProcessEmailJob(Schema):
    kind: Literal["process_email"] = "process_email"
    email_id: str


class ExecuteActionJob(Schema):
    kind: Literal["execute_action"] = "execute_action"
    action_id: int


JobPayload = Annotated[ProcessEmailJob | ExecuteActionJob, Field(discriminator="kind")]


def idempotency_key(payload: ProcessEmailJob | ExecuteActionJob) -> str:
    if isinstance(payload, ProcessEmailJob):
        return f"process_email:{payload.email_id}"
    return f"execute_action:{payload.action_id}"


class ClaimedJob(Schema):
    id: int
    attempts: int
    payload: JobPayload


class JobCounts(Schema):
    queued: int = 0
    running: int = 0
    done: int = 0
    dead: int = 0
