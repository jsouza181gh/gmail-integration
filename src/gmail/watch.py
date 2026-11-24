import os
from collections.abc import Callable

from googleapiclient.discovery import Resource
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from src.db.store import Store
from src.gmail.accounts_store import accounts_needing_renewal

USER_ID = "me"


class WatchResponse(BaseModel):
    historyId: str
    expiration: int


class _Profile(BaseModel):
    emailAddress: str


def start_gmail_watch(service: Resource) -> WatchResponse:
    """Start (or renew) the Gmail watch on INBOX. Returns historyId/expiration."""
    topic = os.getenv("GMAIL_PUBSUB_TOPIC")
    if not topic:
        raise RuntimeError(
            "GMAIL_PUBSUB_TOPIC não definida "
            "(formato: projects/<project_id>/topics/<nome>)."
        )
    request = {
        "topicName": topic,
        "labelIds": ["INBOX"],
        "labelFilterBehavior": "INCLUDE",
    }
    return WatchResponse.model_validate(
        service.users().watch(userId=USER_ID, body=request).execute()
    )


def ensure_watch(store: Store, service: Resource) -> WatchResponse:
    """Start/renew the watch and persist its state.

    First call stores the returned historyId as the sync cursor; renewals
    only refresh the expiration (see Store.init_watch_state).
    """
    response = start_gmail_watch(service)
    profile = _Profile.model_validate(service.users().getProfile(userId=USER_ID).execute())
    store.init_watch_state(profile.emailAddress, response.historyId, response.expiration)
    return response


def renew_due(
    sf: sessionmaker[Session],
    now_ms: int,
    renew_one: Callable[[int], None],
    within_ms: int,
) -> list[str]:
    """Renew the watch of every connected account expiring within `within_ms`.

    One account failing never stops the others; the failures come back as
    text (account id and error kind, no content).
    """
    errors: list[str] = []
    for account in accounts_needing_renewal(sf, now_ms, within_ms):
        try:
            renew_one(account)
        except Exception as exc:  # noqa: BLE001 - isolate accounts from each other
            errors.append(f"conta {account}: {type(exc).__name__}: {exc}")
    return errors
