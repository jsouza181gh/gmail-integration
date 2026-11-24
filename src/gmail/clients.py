"""STUB(fase 2): substituído no merge. Contract signatures only."""
from collections.abc import Callable

from googleapiclient.discovery import Resource
from sqlalchemy.orm import Session, sessionmaker

from src.db.scope import current_account


class GmailRevoked(RuntimeError):
    pass


class GmailClients:
    def __init__(self, sf: sessionmaker[Session]) -> None:
        self.sf = sf

    def service_for(self, account_id: int) -> Resource:
        raise NotImplementedError("STUB(fase 2)")

    def current(self) -> Resource:
        return self.service_for(current_account())

    def has_scope(self, scope: str) -> bool:
        raise NotImplementedError("STUB(fase 2)")

    def getter(self) -> Callable[[], Resource]:
        return self.current
