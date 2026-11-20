"""Helpers shared by the chat tests."""
import struct
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from src.agent.types import LabelInfo, MailItem, MailPage, MailQuery
from src.db.models import Email


def wav(
    seconds: float = 1.0, *, rate: int = 16000, channels: int = 1, bits: int = 16,
    fmt: int = 1,
) -> bytes:
    """A silent PCM WAV with a canonical 44-byte header."""
    block = channels * bits // 8
    data = b"\x00" * (int(rate * seconds) * block)
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, fmt, channels, rate, rate * block, block, bits)
    return header + b"data" + struct.pack("<I", len(data)) + data


def add_email(
    sf: sessionmaker[Session], email_id: str, *, sender: str = "Loja <ofertas@loja.com>",
    subject: str = "50% off", category: str | None = "Promoções",
    has_document: bool | None = False, received_day: int = 1,
) -> None:
    with sf.begin() as s:
        s.add(Email(id=email_id, sender=sender, subject=subject, category=category,
                    has_document=has_document, attempts=0,
                    received_at=datetime(2026, 11, received_day, tzinfo=UTC)))


class SpamMail:
    """MailReader over a fixed set of Gmail messages (spam, trash, old mail)."""

    def __init__(self, items: Sequence[MailItem] = ()) -> None:
        self.items = {i.id: i for i in items}
        self.queries: list[MailQuery] = []

    def item(self, email_id: str) -> MailItem | None:
        return self.items.get(email_id)

    def list_messages(self, query: MailQuery) -> MailPage:
        self.queries.append(query)
        return MailPage(items=tuple(self.items.values()), next_page_token=None,
                        result_size_estimate=len(self.items))

    def get_body(self, email_id: str) -> str:
        return f"corpo de {email_id}"

    def list_labels(self) -> tuple[LabelInfo, ...]:
        return ()


def spam_item(email_id: str, subject: str = "Alerta do Google - Vale") -> MailItem:
    return MailItem(id=email_id, thread_id="t", sender="Google Alerts <googlealerts-noreply@google.com>",
                    subject=subject, date="Thu, 19 Nov 2026 10:00:00 +0000", snippet="alerta",
                    labels=("SPAM",))
