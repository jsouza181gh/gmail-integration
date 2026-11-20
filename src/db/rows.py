"""Typed rows the Store hands out (never ORM objects or dicts)."""
from datetime import date as calendar_date
from datetime import datetime

from pydantic import ConfigDict

from src.llm.types import Schema


class Row(Schema):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class RunRow(Row):
    id: int
    query: str
    page_token: str | None
    pages: int
    fetch_done: bool
    created_at: datetime


class EmailRow(Row):
    id: str
    sender: str | None
    subject: str | None
    date: str | None
    snippet: str | None
    category: str | None
    reason: str | None
    attempts: int
    last_error: str | None
    received_at: datetime | None
    is_relevant: bool | None
    deadline: calendar_date | None
    has_document: bool | None
    ephemeral: bool | None
    category_set_id: int | None
    source: str = "inbox"
    updated_at: datetime


class AgentConfigRow(Row):
    id: int
    version: int
    active: bool
    decide_prompt: str
    chat_prompt: str
    max_turns: int
    created_at: datetime


class AgentRunRow(Row):
    id: int
    trigger: str
    email_id: str | None
    config_id: int
    status: str
    summary: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class AgentStepRow(Row):
    id: int
    run_id: int
    position: int
    kind: str
    name: str
    source: str
    status: str
    detail: str | None
    started_at: datetime
    finished_at: datetime | None


class PendingActionRow(Row):
    id: int
    email_id: str
    sender: str | None
    subject: str | None
    kind: str
    reason: str
    status: str
    origin: str
    rule_id: int | None
    created_at: datetime


class RuleRow(Row):
    id: int
    name: str
    sender_address: str | None
    sender_domain: str | None
    category: str | None
    subject_contains: str | None
    unless_document: bool
    action: str
    mode: str
    active: bool
    fired_count: int
    approved_count: int
    rejected_count: int


class WatchStateRow(Row):
    id: int
    email_address: str
    history_id: str
    expiration: int
    updated_at: datetime


class AttachmentRow(Row):
    id: int
    email_id: str
    part_id: str
    filename: str
    mime_type: str
    size: int
    status: str
    skip_reason: str | None
    doc_class: str | None
    doc_type: str | None
    title: str | None
    summary: str | None
    issuer: str | None
    amount_cents: int | None
    issued_on: calendar_date | None
    due_on: calendar_date | None
    barcode: str | None
    text: str | None
    embedding_model: str | None
    error: str | None
    created_at: datetime
    analyzed_at: datetime | None
