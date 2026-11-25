from datetime import UTC, datetime

import pyotp
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.auth.crypto import verify_password
from src.auth.onboarding import (
    ONBOARDING_STEPS,
    Onboarding,
    OnboardingError,
    is_complete,
    needs_enrollment,
)
from src.db.models import RecoveryCode, User
from tests.api_fakes import SETTINGS
from tests.auth_fakes import make_user

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def test_steps_are_the_contract_and_extensible() -> None:
    assert ONBOARDING_STEPS == ["gmail", "totp", "password_optional", "llm_key", "done"]
    assert is_complete(None) and is_complete(ONBOARDING_STEPS[-1])
    assert not is_complete("totp") and not is_complete("password_optional")
    assert needs_enrollment("gmail") and needs_enrollment("totp")
    assert not needs_enrollment("password_optional") and not needs_enrollment(None)


def test_extending_the_list_moves_the_finish_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.auth.onboarding.ONBOARDING_STEPS", [*ONBOARDING_STEPS, "ready"])
    assert not is_complete("done") and is_complete("ready")


@pytest.fixture
def ob(session_factory: sessionmaker[Session]) -> Onboarding:
    make_user(session_factory, SETTINGS, "ana@example.com", None, email="ana@example.com",
              totp=False, onboarding_step="totp", user_id=5)
    return Onboarding(session_factory, SETTINGS, lambda: NOW)


def _step(sf: sessionmaker[Session]) -> str | None:
    with sf() as s:
        return s.get_one(User, 5).onboarding_step


def test_state_reports_step_and_progress(ob: Onboarding) -> None:
    state = ob.state(5)
    assert (state.step, state.done, state.email) == ("totp", False, "ana@example.com")
    assert state.steps == ONBOARDING_STEPS and state.has_password is False
    assert state.totp_confirmed is False


def test_start_confirm_ack_walks_to_the_password_step(
    ob: Onboarding, session_factory: sessionmaker[Session]
) -> None:
    uri = ob.start_totp(5)
    secret = pyotp.parse_uri(uri).secret
    with pytest.raises(OnboardingError, match="código"):
        ob.confirm_totp(5, "000000")
    with pytest.raises(OnboardingError):
        ob.acknowledge_recovery(5)  # codes were not shown yet
    codes = ob.confirm_totp(5, pyotp.TOTP(secret).at(NOW))
    assert len(codes) == 10 and ob.state(5).totp_confirmed
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(RecoveryCode)) == 10
    assert ob.acknowledge_recovery(5) == "password_optional"
    assert _step(session_factory) == "password_optional"


def test_restarting_totp_replaces_secret_and_codes(
    ob: Onboarding, session_factory: sessionmaker[Session]
) -> None:
    first = pyotp.parse_uri(ob.start_totp(5)).secret
    ob.confirm_totp(5, pyotp.TOTP(first).at(NOW))
    second = pyotp.parse_uri(ob.start_totp(5)).secret  # the page was closed and reopened
    assert first != second and not ob.state(5).totp_confirmed
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(RecoveryCode)) == 0
    with pytest.raises(OnboardingError):
        ob.confirm_totp(5, pyotp.TOTP(first).at(NOW))  # the old authenticator entry is dead


def test_steps_are_enforced_in_order(ob: Onboarding) -> None:
    with pytest.raises(OnboardingError):
        ob.set_password(5, "uma-senha-bem-longa")  # still at totp
    with pytest.raises(OnboardingError):
        ob.skip_password(5)


def _at_password_step(ob: Onboarding) -> None:
    secret = pyotp.parse_uri(ob.start_totp(5)).secret
    ob.confirm_totp(5, pyotp.TOTP(secret).at(NOW))
    ob.acknowledge_recovery(5)


def test_password_is_optional_and_checked(ob: Onboarding, session_factory: sessionmaker[Session]) -> None:
    _at_password_step(ob)
    with pytest.raises(OnboardingError, match="12"):
        ob.set_password(5, "curta")
    assert ob.set_password(5, "uma-senha-bem-longa") == "llm_key"
    with session_factory() as s:
        user = s.get_one(User, 5)
        assert user.password_hash and verify_password(user.password_hash, "uma-senha-bem-longa")
    assert not ob.state(5).done and ob.state(5).has_password  # the Gemini key comes next


def test_skipping_the_password_finishes_without_one(
    ob: Onboarding, session_factory: sessionmaker[Session]
) -> None:
    _at_password_step(ob)
    assert ob.skip_password(5) == "llm_key"
    with session_factory() as s:
        assert s.get_one(User, 5).password_hash is None


def test_finished_users_cannot_re_enroll(ob: Onboarding) -> None:
    _at_password_step(ob)
    ob.skip_password(5)
    with pytest.raises(OnboardingError):
        ob.start_totp(5)  # a finished user changes the second factor elsewhere


def test_a_user_whose_totp_was_reset_can_enroll_again(session_factory: sessionmaker[Session]) -> None:
    make_user(session_factory, SETTINGS, "x@example.com", None, email="x@example.com", totp=False,
              onboarding_step=None, user_id=9)
    ob = Onboarding(session_factory, SETTINGS, lambda: NOW)
    assert ob.begin_enrollment_if_needed(9) is True  # 2FA reset: no secret -> back to the totp step
    with session_factory() as s:
        assert s.get_one(User, 9).onboarding_step == "totp"


def test_begin_enrollment_is_false_for_a_fully_enrolled_user(session_factory: sessionmaker[Session]) -> None:
    make_user(session_factory, SETTINGS, "joao", "uma-senha-bem-longa", user_id=3)
    ob = Onboarding(session_factory, SETTINGS, lambda: NOW)
    assert ob.begin_enrollment_if_needed(3) is False


def test_finishing_a_step_advances_only_from_that_step(
    session_factory: sessionmaker[Session], ob: Onboarding
) -> None:
    assert ob.finish_step(5, "llm_key") == "totp"  # not on it yet: nothing moves
    with session_factory.begin() as s:
        s.get_one(User, 5).onboarding_step = "llm_key"
    assert ob.finish_step(5, "llm_key") == "done"
    assert ob.finish_step(5, "llm_key") == "done"  # a later key change keeps it done
