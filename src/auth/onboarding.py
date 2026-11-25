"""Onboarding steps after the first Google click.

`users.onboarding_step` walks `ONBOARDING_STEPS`; the last one (`done`) is the
finish line. NULL means "finished long ago" (users that existed before invites).
The whole order lives here, so no step can be left without a way forward:
`is_complete` looks at the last entry and every step's owner calls
`next_step`/`finish_step` from its own step.

Steps: `gmail` (done by the Google consent itself, so signup starts at
`totp`), `totp` (secret, first code, recovery codes shown),
`password_optional`, `llm_key` (the user's Gemini key, src/api/routes/llm_key.py),
`finish` (what happens now + optional backfill, src/accounts/finish.py), `done`.
"""
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from src.auth.config import AuthSettings
from src.auth.crypto import hash_password
from src.auth.enroll import (
    begin_totp,
    check_password,
    issue_recovery_codes,
    pending_uri,
    totp_matches,
)
from src.db.models import RecoveryCode, User
from src.llm.types import Schema

ONBOARDING_STEPS: list[str] = ["gmail", "totp", "password_optional", "llm_key", "finish", "done"]
FIRST_STEP_AFTER_SIGNUP = "totp"  # the Google consent connected the Gmail already


class OnboardingError(ValueError):
    """The action does not fit the step the user is on; the message is for the user."""


class WrongCode(OnboardingError):
    """The authenticator code did not match."""


class OnboardingState(Schema):
    step: str | None
    steps: list[str]
    done: bool
    email: str | None
    has_password: bool
    totp_confirmed: bool


def is_complete(step: str | None) -> bool:
    return step is None or step == ONBOARDING_STEPS[-1]


def needs_enrollment(step: str | None) -> bool:
    """Still before (or at) the second-factor step: no session without enrolling."""
    return step is not None and step in ONBOARDING_STEPS and (
        ONBOARDING_STEPS.index(step) <= ONBOARDING_STEPS.index("totp")
    )


def next_step(step: str) -> str:
    i = ONBOARDING_STEPS.index(step)
    return ONBOARDING_STEPS[min(i + 1, len(ONBOARDING_STEPS) - 1)]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Onboarding:
    def __init__(
        self,
        sf: sessionmaker[Session],
        settings: AuthSettings,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.sf = sf
        self.settings = settings
        self._now = clock

    def _user(self, s: Session, user_id: int) -> User:
        user = s.get(User, user_id)
        if user is None:
            raise OnboardingError("Usuário não encontrado.")
        return user

    def _need(self, user: User, step: str) -> None:
        if user.onboarding_step != step:
            raise OnboardingError("Essa etapa não é a atual. Recarregue a página.")

    def _codes(self, s: Session, user_id: int) -> int:
        return s.scalar(
            select(func.count()).select_from(RecoveryCode).where(RecoveryCode.user_id == user_id)
        ) or 0

    def state(self, user_id: int) -> OnboardingState:
        with self.sf() as s:
            user = self._user(s, user_id)
            return OnboardingState(
                step=user.onboarding_step, steps=list(ONBOARDING_STEPS),
                done=is_complete(user.onboarding_step), email=user.email,
                has_password=user.password_hash is not None,
                totp_confirmed=user.onboarding_step == "totp" and self._codes(s, user_id) > 0,
            )

    def begin_enrollment_if_needed(self, user_id: int) -> bool:
        """True when the user must (re)enroll the second factor before getting a session.

        A finished user without a secret (2FA reset by the admin) goes back to
        the `totp` step."""
        with self.sf.begin() as s:
            user = self._user(s, user_id)
            if needs_enrollment(user.onboarding_step):
                return True
            if user.totp_secret_enc is None:
                user.onboarding_step = "totp"
                return True
            return False

    def start_totp(self, user_id: int) -> str:
        """A fresh secret (replacing a pending one) and its otpauth URI."""
        with self.sf.begin() as s:
            user = self._user(s, user_id)
            self._need(user, "totp")
            uri = begin_totp(user, self.settings)
            s.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))
        return uri

    def pending_uri(self, user_id: int) -> str | None:
        with self.sf() as s:
            user = self._user(s, user_id)
            return pending_uri(user, self.settings) if user.onboarding_step == "totp" else None

    def confirm_totp(self, user_id: int, code: str) -> tuple[str, ...]:
        """The first code from the authenticator; returns the recovery codes (shown once)."""
        with self.sf.begin() as s:
            user = self._user(s, user_id)
            self._need(user, "totp")
            if not totp_matches(user, self.settings, code, self._now()):
                raise WrongCode("Esse código não confere. Confira o app e tente de novo.")
            return issue_recovery_codes(s, user_id)

    def acknowledge_recovery(self, user_id: int) -> str:
        with self.sf.begin() as s:
            user = self._user(s, user_id)
            self._need(user, "totp")
            if self._codes(s, user_id) == 0:
                raise OnboardingError("Confirme o código do app antes de seguir.")
            user.onboarding_step = next_step("totp")
            return user.onboarding_step

    def set_password(self, user_id: int, password: str) -> str:
        try:
            check_password(password)
        except ValueError as exc:
            raise OnboardingError(str(exc)) from exc
        with self.sf.begin() as s:
            user = self._user(s, user_id)
            self._need(user, "password_optional")
            user.password_hash = hash_password(password)
            user.password_changed_at = self._now()
            user.onboarding_step = next_step("password_optional")
            return user.onboarding_step

    def skip_password(self, user_id: int) -> str:
        with self.sf.begin() as s:
            user = self._user(s, user_id)
            self._need(user, "password_optional")
            user.onboarding_step = next_step("password_optional")
            return user.onboarding_step

    def finish_step(self, user_id: int, step: str) -> str | None:
        """`step` was done: advance if the user is on it (else leave it as is).

        For steps whose action can also happen later, outside the onboarding
        (the Gemini key is replaced from the settings page)."""
        with self.sf.begin() as s:
            user = self._user(s, user_id)
            if user.onboarding_step == step:
                user.onboarding_step = next_step(step)
            return user.onboarding_step
