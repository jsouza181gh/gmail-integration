"""`oauth_clients`: the Google OAuth clients the platform knows (system scope)."""
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import OAuthClient
from src.db.scope import system_scope
from src.llm.types import Schema
from src.platform.secrets import seal, unseal


class OAuthClientRow(Schema):
    id: int
    kind: str
    client_id: str
    client_secret: str  # decrypted: never log or return it


def _row(c: OAuthClient) -> OAuthClientRow:
    return OAuthClientRow(
        id=c.id, kind=c.kind, client_id=c.client_id, client_secret=unseal(c.client_secret_enc)
    )


def upsert_client(sf: sessionmaker[Session], kind: str, client_id: str, client_secret: str) -> int:
    """One row per (kind, client_id); a changed secret replaces the old one."""
    with system_scope(), sf.begin() as s:
        found = s.scalar(
            select(OAuthClient).where(OAuthClient.kind == kind, OAuthClient.client_id == client_id)
        )
        if found is None:
            found = OAuthClient(kind=kind, client_id=client_id, client_secret_enc=seal(client_secret))
            s.add(found)
            s.flush()
        else:
            found.client_secret_enc = seal(client_secret)
        return found.id


def platform_client(sf: sessionmaker[Session]) -> OAuthClientRow | None:
    with system_scope(), sf() as s:
        found = s.scalar(
            select(OAuthClient).where(OAuthClient.kind == "platform").order_by(OAuthClient.id.desc())
        )
        return _row(found) if found else None


def client_by_id(sf: sessionmaker[Session], client_pk: int) -> OAuthClientRow | None:
    with system_scope(), sf() as s:
        found = s.get(OAuthClient, client_pk)
        return _row(found) if found else None
