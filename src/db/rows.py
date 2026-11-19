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
    updated_at: datetime


class WatchStateRow(Row):
    id: int
    email_address: str
    history_id: str
    expiration: int
    updated_at: datetime
