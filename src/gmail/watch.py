import os

USER_ID = "me"


def start_gmail_watch(service):
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
    return service.users().watch(userId=USER_ID, body=request).execute()


def ensure_watch(store, service):
    """Start/renew the watch and persist its state.

    First call stores the returned historyId as the sync cursor; renewals
    only refresh the expiration (see Store.init_watch_state).
    """
    response = start_gmail_watch(service)
    profile = service.users().getProfile(userId=USER_ID).execute()
    store.init_watch_state(
        profile["emailAddress"], response["historyId"], int(response["expiration"])
    )
    return response
