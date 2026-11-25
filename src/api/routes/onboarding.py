"""/api/onboarding: the steps after the first Google click.

Open to a full session or to the onboarding cookie (Google proved the
identity, the second factor is what this flow enrolls).
"""
from io import BytesIO

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.api.deps import get_deps, onboarding_user, request_ip
from src.api.session_cookies import clear_onboarding, set_session
from src.auth.onboarding import OnboardingError, OnboardingState, WrongCode, is_complete
from src.llm.types import Schema

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


class CodeIn(Schema):
    code: str


class PasswordIn(Schema):
    password: str


class TotpStartOut(Schema):
    secret: str
    uri: str


class RecoveryOut(Schema):
    codes: list[str]


def _failure(exc: OnboardingError) -> HTTPException:
    return HTTPException(400 if isinstance(exc, WrongCode) else 409, str(exc))


def _qr_svg(uri: str) -> bytes:
    image = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = BytesIO()
    image.save(buf)
    return buf.getvalue()


@router.get("/state")
def state(request: Request, user_id: int = Depends(onboarding_user)) -> OnboardingState:
    try:
        return get_deps(request).onboarding.state(user_id)
    except OnboardingError as exc:
        raise _failure(exc) from exc


@router.post("/totp/start")
def totp_start(request: Request, user_id: int = Depends(onboarding_user)) -> TotpStartOut:
    try:
        uri = get_deps(request).onboarding.start_totp(user_id)
    except OnboardingError as exc:
        raise _failure(exc) from exc
    import pyotp

    return TotpStartOut(secret=pyotp.parse_uri(uri).secret, uri=uri)


@router.get("/totp/qr.svg")
def totp_qr(request: Request, user_id: int = Depends(onboarding_user)) -> Response:
    uri = get_deps(request).onboarding.pending_uri(user_id)
    if uri is None:
        raise HTTPException(404, "nenhum QR pendente")
    return Response(_qr_svg(uri), media_type="image/svg+xml")


@router.post("/totp/confirm")
def totp_confirm(
    body: CodeIn, request: Request, user_id: int = Depends(onboarding_user)
) -> RecoveryOut:
    try:
        codes = get_deps(request).onboarding.confirm_totp(user_id, body.code[:16])
    except OnboardingError as exc:
        raise _failure(exc) from exc
    return RecoveryOut(codes=list(codes))


@router.post("/recovery/ack", status_code=204)
def recovery_ack(request: Request, response: Response, user_id: int = Depends(onboarding_user)) -> None:
    """"I saved my codes": the second factor is enrolled, so a real session starts now."""
    deps = get_deps(request)
    try:
        deps.onboarding.acknowledge_recovery(user_id)
    except OnboardingError as exc:
        raise _failure(exc) from exc
    issued = deps.auth.start_session(user_id, request_ip(request), request.headers.get("user-agent", ""))
    clear_onboarding(response)
    set_session(response, deps, issued)


@router.post("/password", status_code=204)
def password(body: PasswordIn, request: Request, user_id: int = Depends(onboarding_user)) -> None:
    deps = get_deps(request)
    try:
        step = deps.onboarding.set_password(user_id, body.password[:256])
    except OnboardingError as exc:
        raise _failure(exc) from exc
    _finished(request, user_id, step)


@router.post("/password/skip", status_code=204)
def password_skip(request: Request, user_id: int = Depends(onboarding_user)) -> None:
    try:
        step = get_deps(request).onboarding.skip_password(user_id)
    except OnboardingError as exc:
        raise _failure(exc) from exc
    _finished(request, user_id, step)


def _finished(request: Request, user_id: int, step: str) -> None:
    if is_complete(step):
        get_deps(request).log.record("onboarding_done", request_ip(request), f"usuário {user_id}", user_id)
