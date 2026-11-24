"""/api/gmail/status: whether the user's Gmail is connected."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.deps import get_deps, require_user
from src.db.models import GmailAccount
from src.db.scope import current_account, system_scope
from src.llm.types import Schema

router = APIRouter(prefix="/api/gmail", dependencies=[Depends(require_user)], tags=["gmail"])
NOT_LINKED = "O Gmail ainda não foi ligado a este novo login: clique em Reconectar Gmail."


class GmailStatus(Schema):
    email_address: str
    status: str  # connected | disconnected | revoked | template
    last_sync_at: datetime | None
    last_error: str | None


@router.get("/status")
def status(request: Request) -> GmailStatus:
    account_id = current_account()
    with system_scope(), get_deps(request).sf() as s:
        acc = s.get(GmailAccount, account_id)
        if acc is None:
            raise HTTPException(404, "conta não encontrada")
        # an account that predates per-account credentials has no token yet
        unlinked = acc.status == "connected" and acc.refresh_token_enc is None
        return GmailStatus(
            email_address=acc.email_address,
            status="disconnected" if unlinked else acc.status,
            last_sync_at=acc.last_sync_at,
            last_error=NOT_LINKED if unlinked else acc.last_error,
        )
