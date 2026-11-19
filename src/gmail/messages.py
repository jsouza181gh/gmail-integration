"""Gmail message helpers shared by the sync and the CLI."""
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from googleapiclient.discovery import Resource
from pydantic import BaseModel

from src.llm.types import Schema

USER_ID = "me"
SUMMARY_HEADERS = ["From", "Subject", "Date"]
SNIPPET_MAX_CHARS = 200
_INVISIBLE_RE = re.compile(r"[​-‏⁠﻿⠀\xa0]")


class _Header(BaseModel):
    name: str
    value: str


class _Payload(BaseModel):
    headers: list[_Header] = []


class _GmailMessage(BaseModel):
    """The slice of a `messages.get(format=metadata)` response we read."""

    id: str
    snippet: str | None = None
    payload: _Payload = _Payload()


class MessageSummary(Schema):
    id: str
    sender: str
    subject: str
    date: str
    snippet: str
    received_at: datetime | None = None


def _parse_date(value: str | None) -> datetime | None:
    """RFC 2822 Date header -> aware UTC datetime; None when unparseable."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def message_summary(service: Resource, message_id: str) -> MessageSummary:
    raw = service.users().messages().get(
        userId=USER_ID,
        id=message_id,
        format="metadata",
        metadataHeaders=SUMMARY_HEADERS,
    ).execute(num_retries=5)
    message = _GmailMessage.model_validate(raw)
    headers = {h.name: h.value for h in message.payload.headers}
    snippet = _INVISIBLE_RE.sub("", message.snippet or "")
    return MessageSummary(
        id=message.id,
        sender=(headers.get("From") or "")[:60],
        subject=(headers.get("Subject") or "")[:100],
        date=(headers.get("Date") or "")[:25],
        snippet=" ".join(snippet.split())[:SNIPPET_MAX_CHARS],
        received_at=_parse_date(headers.get("Date")),
    )
