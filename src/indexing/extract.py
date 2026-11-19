"""Attachment bytes -> text we can read, or the file itself for the model.

PDFs with a text layer are read locally (pypdf, cheap); scanned, encrypted
or broken PDFs and images go to Gemini as files (OCR there).
"""
import io
from typing import Literal

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from src.llm.types import BinaryPart, Schema

PDF_MAX_PAGES = 15
TEXT_MAX_CHARS = 12_000
MIN_TEXT_CHARS = 200  # less than this in a PDF: treat as scanned

ExtractMode = Literal["text", "binary"]


class Extracted(Schema):
    mode: ExtractMode
    text: str
    part: BinaryPart | None


def _pdf_text(data: bytes) -> str | None:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return None
        pages = reader.pages[:PDF_MAX_PAGES]
        return "\n".join(p.extract_text() or "" for p in pages)
    except (PyPdfError, ValueError, KeyError, TypeError, OSError):
        return None


def _squeeze(text: str) -> str:
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)[:TEXT_MAX_CHARS]


def extract(mime_type: str, filename: str, data: bytes) -> Extracted:
    if mime_type == "application/pdf":
        text = _pdf_text(data)
        if text is not None and len("".join(text.split())) >= MIN_TEXT_CHARS:
            return Extracted(mode="text", text=_squeeze(text), part=None)
        return Extracted(mode="binary", text="", part=BinaryPart(mime_type=mime_type, data=data))
    if mime_type in ("application/xml", "text/xml"):
        return Extracted(mode="text", text=_squeeze(data.decode("utf-8", "replace")), part=None)
    return Extracted(mode="binary", text="", part=BinaryPart(mime_type=mime_type, data=data))
