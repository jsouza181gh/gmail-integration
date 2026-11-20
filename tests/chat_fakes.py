"""Helpers shared by the chat tests."""
import struct
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

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
