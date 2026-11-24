"""/api/account: leave the service (disconnect Gmail, or delete everything)."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select

from src.accounts.exit import (
    BACKUP_COPIES_KEPT,
    AccountProtected,
    account_email,
    delete_account,
    disconnect,
)
from src.api.deps import ApiDeps, get_deps, request_ip, require_user
from src.api.routes.auth import _clear_session
from src.db.models import GmailAccount, User
from src.db.scope import current_account, system_scope
from src.gmail.clients import GmailClients
from src.llm.types import Schema

router = APIRouter(prefix="/api/account", tags=["account"])


class AccountInfo(Schema):
    email: str
    status: str
    can_delete: bool  # the admin account cannot be deleted through the panel
    backup_copies: int  # database dumps kept by the deploy


class DeleteIn(Schema):
    email: str
    password: str | None = None
    code: str | None = None


def _clients(deps: ApiDeps) -> GmailClients:
    if deps.clients is None:
        raise HTTPException(503, "Gmail indisponível nesta instância da API")
    return deps.clients


def _info(deps: ApiDeps, user_id: int) -> AccountInfo:
    with system_scope(), deps.sf() as s:
        user = s.get(User, user_id)
        account = s.scalars(select(GmailAccount).where(GmailAccount.user_id == user_id)).first()
        if user is None or account is None:
            raise HTTPException(404, "conta não encontrada")
        return AccountInfo(
            email=account.email_address,
            status=account.status,
            can_delete=user.role != "admin",
            backup_copies=BACKUP_COPIES_KEPT,
        )


@router.get("")
def info(request: Request, user_id: int = Depends(require_user)) -> AccountInfo:
    return _info(get_deps(request), user_id)


@router.post("/disconnect", status_code=204)
def disconnect_gmail(request: Request, user_id: int = Depends(require_user)) -> None:
    deps = get_deps(request)
    disconnect(deps.sf, _clients(deps), current_account())
    deps.log.record("account_disconnected", request_ip(request), f"conta {current_account()}")


@router.post("/delete", status_code=204)
def delete(
    body: DeleteIn, request: Request, response: Response, user_id: int = Depends(require_user)
) -> None:
    deps = get_deps(request)
    clients = _clients(deps)
    current = _info(deps, user_id)
    if not current.can_delete:
        raise HTTPException(403, "a conta do administrador não pode ser excluída pelo painel")
    if body.email.strip().lower() != (account_email(deps.sf, user_id) or "").lower():
        raise HTTPException(422, "o e-mail digitado não confere com o da conta")
    ip = request_ip(request)
    if not deps.auth.confirm_identity(user_id, body.password[:256] if body.password else None,
                                      body.code[:64] if body.code else None, ip):
        if deps.auth.check_open(ip) != "ok":
            raise HTTPException(423, "login bloqueado")
        raise HTTPException(401, "senha ou código inválido")
    try:
        delete_account(deps.sf, clients, user_id)
    except AccountProtected as exc:
        raise HTTPException(403, str(exc)) from exc
    deps.log.record("account_deleted", ip, f"usuário {user_id}")
    _clear_session(response)
