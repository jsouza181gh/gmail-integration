"""Read-only Gmail access for the agent (satisfies `ports.MailReader`).

Bodies come from `messages.get(format=full)`: text/plain preferred, HTML
reduced to text otherwise, invisible characters removed, truncated.
"""
import base64
import binascii
import re
from collections.abc import Callable
from html.parser import HTMLParser

from googleapiclient.discovery import Resource
from pydantic import BaseModel

from src.agent.types import LabelInfo, MailItem, MailPage, MailQuery

USER_ID = "me"
BODY_MAX_CHARS = 6000
MAX_RESULTS_LIMIT = 100
SUMMARY_HEADERS = ["From", "Subject", "Date"]
_INVISIBLE_RE = re.compile(r"[​-‏⁠﻿⠀\xa0]")
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table", "section"}


# --- slices of Gmail API responses we read -----------------------------------

class _Body(BaseModel):
    data: str | None = None


class _Header(BaseModel):
    name: str
    value: str


class _Part(BaseModel):
    mimeType: str = ""
    filename: str | None = None
    body: _Body = _Body()
    headers: list[_Header] = []
    parts: list["_Part"] = []


class _Message(BaseModel):
    id: str
    threadId: str | None = None
    labelIds: list[str] = []
    snippet: str | None = None
    payload: _Part = _Part()


class _Ref(BaseModel):
    id: str


class _ListPage(BaseModel):
    messages: list[_Ref] = []
    nextPageToken: str | None = None
    resultSizeEstimate: int = 0


class _Label(BaseModel):
    id: str
    name: str
    type: str | None = None


class _Labels(BaseModel):
    labels: list[_Label] = []


# --- HTML -> text ---------------------------------------------------------------

class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.chunks.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return "".join(parser.chunks)


def _clean(text: str) -> str:
    text = _INVISIBLE_RE.sub(" ", text)
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _decode(data: str | None) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return ""


def _find(part: _Part, mime: str) -> str | None:
    if part.mimeType == mime and not part.filename and part.body.data:
        return _decode(part.body.data)
    for child in part.parts:
        found = _find(child, mime)
        if found is not None:
            return found
    return None


def extract_body(payload: _Part) -> str:
    plain = _find(payload, "text/plain")
    if plain is not None and plain.strip():
        text = plain
    else:
        html = _find(payload, "text/html")
        text = html_to_text(html) if html else ""
    return _clean(text)[:BODY_MAX_CHARS]


class GmailReader:
    def __init__(self, service_factory: Callable[[], Resource]) -> None:
        self._service = service_factory

    def get_body(self, email_id: str) -> str:
        raw = self._service().users().messages().get(
            userId=USER_ID, id=email_id, format="full"
        ).execute(num_retries=5)
        return extract_body(_Message.model_validate(raw).payload)

    def _item(self, service: Resource, message_id: str) -> MailItem:
        raw = service.users().messages().get(
            userId=USER_ID, id=message_id, format="metadata", metadataHeaders=SUMMARY_HEADERS
        ).execute(num_retries=5)
        msg = _Message.model_validate(raw)
        headers = {h.name: h.value for h in msg.payload.headers}
        return MailItem(
            id=msg.id,
            thread_id=msg.threadId,
            sender=(headers.get("From") or "")[:120],
            subject=(headers.get("Subject") or "")[:200],
            date=(headers.get("Date") or "")[:40],
            snippet=_clean(msg.snippet or "")[:200],
            labels=tuple(msg.labelIds),
        )

    def list_messages(self, query: MailQuery) -> MailPage:
        service = self._service()
        params: dict[str, str | int | bool | list[str]] = {
            "userId": USER_ID,
            "maxResults": max(1, min(query.max_results, MAX_RESULTS_LIMIT)),
            "includeSpamTrash": query.include_spam_trash,
        }
        if query.query:
            params["q"] = query.query
        if query.label_ids:
            params["labelIds"] = list(query.label_ids)
        if not query.query and not query.label_ids:
            params["labelIds"] = ["INBOX"]
        if query.page_token:
            params["pageToken"] = query.page_token
        page = _ListPage.model_validate(
            service.users().messages().list(**params).execute(num_retries=5)
        )
        return MailPage(
            items=tuple(self._item(service, m.id) for m in page.messages),
            next_page_token=page.nextPageToken,
            result_size_estimate=page.resultSizeEstimate,
        )

    def list_labels(self) -> tuple[LabelInfo, ...]:
        raw = self._service().users().labels().list(userId=USER_ID).execute(num_retries=5)
        return tuple(
            LabelInfo(id=label.id, name=label.name, type=label.type)
            for label in _Labels.model_validate(raw).labels
        )
