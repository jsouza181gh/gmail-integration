"""One `messages.get(format=full)` -> summary + body text + attachment refs.

Attachments are referenced by `partId`: Gmail issues a new `attachmentId` on
every read, so the id is resolved again at download time.
"""
import base64
import binascii
from collections.abc import Iterator, Mapping

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from pydantic import BaseModel

from src.gmail.messages import MessageSummary, summary_from
from src.gmail.reader import _Message, _Part, extract_body
from src.llm.types import Schema

USER_ID = "me"
BODY_STORE_MAX = 20_000


class AttachmentRef(Schema):
    part_id: str
    filename: str
    mime_type: str
    size: int
    inline: bool  # embedded in the HTML (logos, signatures), not a document


class FullMessage(Schema):
    summary: MessageSummary
    body: str
    attachments: tuple[AttachmentRef, ...]


class _AttachmentBody(BaseModel):
    data: str = ""


def _walk(part: _Part) -> Iterator[_Part]:
    yield part
    for child in part.parts:
        yield from _walk(child)


def _is_attachment(part: _Part) -> bool:
    return bool(part.filename) and bool(part.body.attachmentId or part.body.data)


def _ref(part: _Part) -> AttachmentRef:
    headers = {h.name.lower(): h.value for h in part.headers}
    disposition = headers.get("content-disposition", "").lower()
    return AttachmentRef(
        part_id=part.partId,
        filename=part.filename or "",
        mime_type=part.mimeType.lower(),
        size=part.body.size,
        inline="content-id" in headers and not disposition.startswith("attachment"),
    )


def parse_full(raw: Mapping[str, object]) -> FullMessage:
    msg = _Message.model_validate(raw)
    headers = {h.name: h.value for h in msg.payload.headers}
    return FullMessage(
        summary=summary_from(msg.id, msg.snippet, headers),
        body=extract_body(msg.payload, max_chars=BODY_STORE_MAX),
        attachments=tuple(_ref(p) for p in _walk(msg.payload) if _is_attachment(p)),
    )


def _get(service: Resource, message_id: str) -> Mapping[str, object]:
    raw: Mapping[str, object] = service.users().messages().get(
        userId=USER_ID, id=message_id, format="full"
    ).execute(num_retries=5)
    return raw


def fetch_full(service: Resource, message_id: str) -> FullMessage | None:
    """None when the message no longer exists."""
    try:
        return parse_full(_get(service, message_id))
    except HttpError as e:
        if e.resp.status == 404:
            return None
        raise


def _decode(data: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("anexo com base64 inválido") from exc


def fetch_attachment(service: Resource, message_id: str, part_id: str) -> bytes:
    """Bytes of one attachment; LookupError when the message or part is gone."""
    try:
        raw = _get(service, message_id)
    except HttpError as e:
        if e.resp.status == 404:
            raise LookupError(f"mensagem {message_id} não existe mais") from e
        raise
    part = next(
        (p for p in _walk(_Message.model_validate(raw).payload) if p.partId == part_id), None
    )
    if part is None or not _is_attachment(part):
        raise LookupError(f"parte {part_id} não existe em {message_id}")
    if part.body.data:  # small attachments come inline
        return _decode(part.body.data)
    body = _AttachmentBody.model_validate(
        service.users().messages().attachments().get(
            userId=USER_ID, messageId=message_id, id=part.body.attachmentId
        ).execute(num_retries=5)
    )
    return _decode(body.data)
