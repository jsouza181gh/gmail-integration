"""Gmail clients per account.

The refresh token of each account lives sealed in `gmail_accounts`; this
factory turns it into a `Resource`. Access tokens (about an hour) are kept in
memory only. A refresh token Google rotates is sealed back into the database;
`invalid_grant` means the user (or Google) revoked access: the account becomes
`revoked`, `on_revoked` raises the operational alert and `GmailRevoked` is
raised.

googleapiclient services are not thread-safe: every call builds a new one.
"""
import threading
from collections.abc import Callable
from datetime import datetime

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import GmailAccount
from src.db.scope import current_account, system_scope
from src.gmail.accounts_store import mark_status, save_refresh_token
from src.gmail.oauth import GMAIL_MODIFY, TOKEN_URL
from src.gmail.oauth_clients import client_by_id
from src.platform.secrets import unseal
from src.utils.console import log

type Refresher = Callable[[Credentials], None]
type ServiceBuilder = Callable[[Credentials], Resource]
type RevokedHook = Callable[[int, str], None]


class GmailRevoked(RuntimeError):
    """Google no longer accepts this account's token: the user must reconnect."""


class GmailNotConnected(RuntimeError):
    """The account has no usable Gmail credentials yet."""


def _refresh(creds: Credentials) -> None:
    creds.refresh(Request())


def _build(creds: Credentials) -> Resource:
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


class _Cached:
    def __init__(self, fingerprint: str, token: str, expiry: datetime | None) -> None:
        self.fingerprint = fingerprint
        self.token = token
        self.expiry = expiry


class GmailClients:
    def __init__(
        self,
        sf: sessionmaker[Session],
        *,
        refresh: Refresher = _refresh,
        build_service: ServiceBuilder = _build,
        on_revoked: RevokedHook | None = None,
    ) -> None:
        self.sf = sf
        self._refresh = refresh
        self._build = build_service
        self.on_revoked = on_revoked  # (account_id, email): set after the alerts exist
        self._lock = threading.Lock()
        self._access: dict[int, _Cached] = {}

    def service_for(self, account_id: int) -> Resource:
        with system_scope(), self.sf() as s:
            acc = s.get(GmailAccount, account_id)
            if acc is None:
                raise GmailNotConnected(f"conta {account_id} não existe")
            email, status = acc.email_address, acc.status
            sealed, scopes, client_pk = acc.refresh_token_enc, acc.scopes, acc.oauth_client_id
        if status == "revoked":
            raise GmailRevoked(f"o acesso de {email} ao Gmail foi revogado; reconecte")
        if status != "connected" or sealed is None or client_pk is None:
            raise GmailNotConnected(f"{email} não tem o Gmail conectado")
        if GMAIL_MODIFY not in (scopes or "").split():
            raise GmailNotConnected(f"{email} não deu a permissão necessária; reconecte o Gmail")
        client = client_by_id(self.sf, client_pk)
        if client is None:
            raise GmailNotConnected("cliente OAuth da conta não encontrado")
        refresh_token = unseal(sealed)

        with self._lock:
            hit = self._access.get(account_id)
        creds = Credentials(
            token=hit.token if hit and hit.fingerprint == sealed else None,
            expiry=hit.expiry if hit and hit.fingerprint == sealed else None,
            refresh_token=refresh_token,
            token_uri=TOKEN_URL,
            client_id=client.client_id,
            client_secret=client.client_secret,
        )
        if not creds.valid:
            self._renew(account_id, email, sealed, creds, refresh_token)
        return self._build(creds)

    def _renew(
        self, account_id: int, email: str, sealed: str, creds: Credentials, old_refresh: str
    ) -> None:
        try:
            self._refresh(creds)
        except RefreshError as exc:
            if "invalid_grant" not in str(exc):
                raise
            mark_status(self.sf, account_id, "revoked", "Google revogou o acesso (invalid_grant)")
            with self._lock:
                self._access.pop(account_id, None)
            hook = self.on_revoked
            if hook is not None:
                try:
                    hook(account_id, email)
                except Exception as hook_exc:  # noqa: BLE001 - alerting must not mask the cause
                    log(f"alerta de Gmail revogado não enviado: {hook_exc}")
            raise GmailRevoked(f"o acesso de {email} ao Gmail foi revogado; reconecte") from exc
        if not creds.valid or creds.token is None:
            raise GmailNotConnected("não foi possível obter credenciais válidas")
        if creds.refresh_token and creds.refresh_token != old_refresh:
            save_refresh_token(self.sf, account_id, creds.refresh_token)
            sealed = ""  # the stored token changed: the cache key is stale
        with self._lock:
            self._access[account_id] = _Cached(sealed, creds.token, creds.expiry)

    def current(self) -> Resource:
        return self.service_for(current_account())

    def has_scope(self, scope: str) -> bool:
        """Whether the current account's token was granted `scope`."""
        account_id = current_account()
        with system_scope(), self.sf() as s:
            acc = s.get(GmailAccount, account_id)
            return acc is not None and scope in (acc.scopes or "").split()

    def getter(self) -> Callable[[], Resource]:
        return self.current
