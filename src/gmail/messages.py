"""Gmail message helpers shared by the sync and the CLI."""
import re

USER_ID = "me"
SUMMARY_HEADERS = ["From", "Subject", "Date"]
SNIPPET_MAX_CHARS = 80
_INVISIBLE_RE = re.compile(r"[​-‏⁠﻿⠀\xa0]")


def message_summary(service, message_id):
    message = service.users().messages().get(
        userId=USER_ID,
        id=message_id,
        format="metadata",
        metadataHeaders=SUMMARY_HEADERS,
    ).execute(num_retries=5)
    headers = {
        h["name"]: h["value"]
        for h in message.get("payload", {}).get("headers", [])
    }
    snippet = _INVISIBLE_RE.sub("", message.get("snippet") or "")
    return {
        "id": message["id"],
        "from": (headers.get("From") or "")[:60],
        "subject": (headers.get("Subject") or "")[:100],
        "date": (headers.get("Date") or "")[:25],
        "snippet": " ".join(snippet.split())[:SNIPPET_MAX_CHARS],
    }
