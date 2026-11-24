"""STUB(fase 2): substituído no merge. Contract signatures only."""
from sqlalchemy.orm import Session, sessionmaker

from src.gmail.clients import GmailClients


def stop_watch(clients: GmailClients, account_id: int) -> None:
    raise NotImplementedError("STUB(fase 2)")


def revoke_token(sf: sessionmaker[Session], account_id: int) -> None:
    raise NotImplementedError("STUB(fase 2)")
