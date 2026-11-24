"""Users for tests: what the invite flow produces, built directly."""
from collections.abc import Callable
from datetime import UTC, datetime

import pyotp
from sqlalchemy.orm import Session, sessionmaker

from src.auth.config import AuthSettings
from src.auth.crypto import hash_password
from src.auth.enroll import Enrollment, begin_totp, issue_recovery_codes


def _utcnow() -> datetime:
    return datetime.now(UTC)


def make_user(
    sf: sessionmaker[Session],
    settings: AuthSettings,
    username: str,
    password: str | None,
    *,
    user_id: int | None = None,
    role: str = "member",
    email: str | None = None,
    totp: bool = True,
    onboarding_step: str | None = None,
    google_sub: str | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> Enrollment:
    from src.db.models import User

    now = clock()
    with sf.begin() as s:
        user = User(
            username=username, email=email, role=role, created_at=now, password_changed_at=now,
            password_hash=hash_password(password) if password else None,
            onboarding_step=onboarding_step, google_sub=google_sub,
        )
        if user_id is not None:
            user.id = user_id
        s.add(user)
        s.flush()
        uri = begin_totp(user, settings) if totp else ""
        codes = issue_recovery_codes(s, user.id) if totp else ()
    return Enrollment(otpauth_uri=uri, recovery_codes=codes)


def secret_of(enrollment: Enrollment) -> str:
    return pyotp.parse_uri(enrollment.otpauth_uri).secret
