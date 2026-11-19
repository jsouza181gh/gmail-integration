"""Raw Gmail API message dicts for indexing tests."""
import base64


def b64(data: bytes | str) -> str:
    raw = data.encode() if isinstance(data, str) else data
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def header(name: str, value: str) -> dict[str, str]:
    return {"name": name, "value": value}


def attachment_part(
    part_id: str, filename: str, mime: str, size: int, *, att_id: str = "ATT",
    inline_cid: bool = False,
) -> dict[str, object]:
    headers = [header("Content-Disposition", f'{"inline" if inline_cid else "attachment"}; filename="{filename}"')]
    if inline_cid:
        headers.append(header("Content-ID", "<img1>"))
    return {
        "partId": part_id,
        "mimeType": mime,
        "filename": filename,
        "headers": headers,
        "body": {"attachmentId": f"{att_id}-{part_id}", "size": size},
    }


def full_message(
    mid: str,
    body: str = "corpo do e-mail",
    attachments: list[dict[str, object]] | None = None,
    *,
    system: bool = False,
    subject: str | None = None,
) -> dict[str, object]:
    headers = [
        header("From", f"Loja <{mid}@x.com>"),
        header("Subject", subject or f"assunto {mid}"),
        header("Date", "Mon, 16 Nov 2026 10:00:00 +0000"),
    ]
    if system:
        headers.append(header("X-Gmail-Integration", "alert"))
    return {
        "id": mid,
        "threadId": f"t-{mid}",
        "snippet": f"snip {mid}",
        "labelIds": ["INBOX"],
        "payload": {
            "partId": "",
            "mimeType": "multipart/mixed",
            "headers": headers,
            "body": {"size": 0},
            "parts": [
                {"partId": "0", "mimeType": "text/plain", "filename": "", "headers": [],
                 "body": {"data": b64(body), "size": len(body)}},
                *(attachments or []),
            ],
        },
    }
