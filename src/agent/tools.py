from src.gmail.client import get_gmail_service

from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_agent
from langchain.tools import tool
from email.mime.text import MIMEText
from dotenv import load_dotenv
import base64
import json
import re

from langchain_core.callbacks import BaseCallbackHandler

class AgentLogger(BaseCallbackHandler):

    def on_llm_start(self, serialized, prompts, **kwargs):
        print("[LLM] Iniciando chamada")

    def on_llm_end(self, response, **kwargs):
        print("[LLM] Chamada finalizada")

    def on_tool_start(self, serialized, input_str, **kwargs):
        print(f"[TOOL] Executando: {serialized.get('name')}")

    def on_tool_end(self, output, **kwargs):
        text = str(output)
        suffix = "..." if len(text) > LOG_MAX_CHARS else ""
        print(f"[TOOL] Resultado ({len(text)} chars): {text[:LOG_MAX_CHARS]}{suffix}")

    def on_tool_error(self, error, **kwargs):
        print(f"[TOOL ERROR] {error}")


load_dotenv()

USER_ID = "me"

MAX_RESULTS_LIMIT = 100
SUMMARY_HEADERS = ["From", "Subject", "Date"]
SNIPPET_MAX_CHARS = 80
LOG_MAX_CHARS = 300
_INVISIBLE_RE = re.compile(r"[​-‏⁠﻿⠀\xa0]")


def _message_summary(service, message_id):
    message = service.users().messages().get(
        userId=USER_ID,
        id=message_id,
        format="metadata",
        metadataHeaders=SUMMARY_HEADERS
    ).execute()
    headers = {
        h["name"]: h["value"]
        for h in message.get("payload", {}).get("headers", [])
    }
    snippet = _INVISIBLE_RE.sub("", message.get("snippet") or "")
    snippet = " ".join(snippet.split())[:SNIPPET_MAX_CHARS]
    return {
        "id": message["id"],
        "from": (headers.get("From") or "")[:60],
        "subject": (headers.get("Subject") or "")[:100],
        "date": (headers.get("Date") or "")[:25],
        "snippet": snippet,
    }


@tool(parse_docstring=True)
def list_emails(
    query: str | None = None,
    label_ids: list[str] | None = None,
    max_results: int = 10,
    page_token: str | None = None,
    include_spam_trash: bool = False,
    include_details: bool = True,
) -> dict:
    """List or search emails in the user's Gmail account.

    Filters (query and label_ids) are combined with AND. If neither is given,
    the INBOX is listed. Results are ordered from newest to oldest.

    Args:
        query: Gmail search string, same syntax as the Gmail search box.
            Operators (combine with spaces; use OR, -, and parentheses):
            from:ana@x.com, to:, cc:, subject:invoice, "exact phrase",
            is:unread, is:read, is:starred, is:important,
            in:inbox, in:sent, in:drafts, in:trash, in:spam, in:anywhere,
            label:work, category:primary|social|promotions|updates|forums,
            has:attachment, filename:pdf, larger:5M, smaller:1M,
            after:2025/01/31, before:2025/02/28,
            newer_than:2d, older_than:1y (d=days, m=months, y=years),
            e.g. "is:unread from:boss@x.com newer_than:7d".
        label_ids: Label IDs that every message must have, e.g. ["INBOX",
            "UNREAD"], ["STARRED"], ["SENT"], ["IMPORTANT"],
            ["CATEGORY_PROMOTIONS"] or custom IDs from list_labels.
        max_results: Number of emails to return (1-100). Default 10.
        page_token: nextPageToken from a previous call, to get the next page.
        include_spam_trash: Also search SPAM and TRASH (excluded by default).
        include_details: If true, each email includes from/to/subject/date/
            snippet/labels. If false, only id and threadId (faster).

    Returns:
        A dict with "emails" (list), "nextPageToken" (None when there are no
        more pages) and "resultSizeEstimate" (approximate total matches).
    """
    service = get_gmail_service()
    max_results = max(1, min(max_results, MAX_RESULTS_LIMIT))

    params = {
        "userId": USER_ID,
        "maxResults": max_results,
        "includeSpamTrash": include_spam_trash,
    }
    if query:
        params["q"] = query
    if label_ids:
        params["labelIds"] = label_ids
    if not query and not label_ids:
        params["labelIds"] = ["INBOX"]
    if page_token:
        params["pageToken"] = page_token

    results = service.users().messages().list(**params).execute()
    messages = results.get("messages", [])

    if include_details:
        messages = [_message_summary(service, m["id"]) for m in messages]

    return {
        "emails": messages,
        "nextPageToken": results.get("nextPageToken"),
        "resultSizeEstimate": results.get("resultSizeEstimate", 0),
    }


@tool
def list_labels() -> list[dict]:
    """
    List all labels (system and custom) of the user's Gmail account.
    Use it to find label IDs for the label_ids filter of list_emails.
    Returns:
        A list of dicts with label id, name and type (system or user).
    """
    service = get_gmail_service()
    results = service.users().labels().list(userId=USER_ID).execute()
    return [
        {"id": l["id"], "name": l["name"], "type": l.get("type")}
        for l in results.get("labels", [])
    ]


@tool
def get_email(message_id):
    """
    Get the details of a specific email message.
    Args:
        message_id (str): The ID of the email message to retrieve.
    Returns:
        A dictionary containing the email details.
    """
    service = get_gmail_service()
    message = service.users().messages().get(
        userId=USER_ID,
        id=message_id,
        format="full"
    ).execute()

    headers = message["payload"].get("headers", [])

    return {
        "id": message["id"],
        "snippet": message.get("snippet"),
        "headers": {
            h["name"]: h["value"] for h in headers
        }
    }


def mark_as_read(message_id):
    service = get_gmail_service()
    service.users().messages().modify(
        userId=USER_ID,
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]}
    ).execute()


def archive_email(message_id):
    service = get_gmail_service()
    service.users().messages().modify(
        userId=USER_ID,
        id=message_id,
        body={"removeLabelIds": ["INBOX"]}
    ).execute()


def trash_email(message_id):
    service = get_gmail_service()
    service.users().messages().trash(
        userId=USER_ID,
        id=message_id
    ).execute()


def send_email(to, subject, body):
    service = get_gmail_service()
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject

    raw_message = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode()

    service.users().messages().send(
        userId=USER_ID,
        body={"raw": raw_message}
    ).execute()


SYSTEM_PROMPT = """
You are a specialized email assistant with read-only access to the user's Gmail.
Tools: list_emails (search/filter/paginate), list_labels, get_email.

When asked to review or classify unread emails, follow this procedure:
1. Call list_emails with query="is:unread in:inbox", max_results=50 and
   include_details=true (from, subject, date and snippet are enough to classify).
2. If nextPageToken is not null, call list_emails again with the same query and
   page_token=nextPageToken. Repeat until nextPageToken is null. Never stop
   after the first page and never invent emails that were not returned.
3. Use get_email only when from/subject/snippet are not enough to classify an
   email, and only for that email.
4. Classify EVERY unread email into exactly one category:
   - Urgente: needs action or reply soon (deadlines, security alerts, payment
     problems, direct questions from real people, meeting changes).
   - Trabalho: work-related, not urgent (projects, clients, team threads).
   - Financeiro: invoices, receipts, bank, billing, subscriptions charges.
   - Pessoal: friends, family, personal matters.
   - Notificações: automated alerts from apps, services, social networks.
   - Newsletter/Promoções: marketing, newsletters, offers, mailing lists.
   - Outros: anything that fits none of the above.
5. Check that the number of classified emails equals the number of unread
   emails fetched. If not, fix it before answering.

Final answer, in Portuguese (pt-BR):
- Start with the total of unread emails and the count per category.
- Then, for each category (Urgente first), list every email as:
  remetente — assunto — data — motivo curto da classificação.
- End with a short list of suggested next actions (what to reply to first,
  what can be archived).
Do not modify, archive, delete or send anything.
"""

USER_PROMPT = (
    "Classifique todos os meus e-mails não lidos da caixa de entrada, "
    "percorrendo todas as páginas de resultados."
)
