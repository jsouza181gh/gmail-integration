import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.auth.onboarding import ONBOARDING_STEPS
from src.db.models import User
from src.llm.keys import AccountKeys, keys_for_account
from tests.api_fakes import SETTINGS, Api

BAD = "Essa chave não existe ou foi copiada incompleta."


class Validator:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def __call__(self, key: str) -> str | None:
        self.seen.append(key)
        return BAD if key.startswith("bad") else None


@pytest.fixture
def validator() -> Validator:
    return Validator()


@pytest.fixture
def api(
    session_factory: sessionmaker[Session], validator: Validator, monkeypatch: pytest.MonkeyPatch
) -> Api:
    monkeypatch.setenv("APP_SECRET", SETTINGS.app_secret)
    a = Api(session_factory, key_validator=validator)
    a.login()
    return a


def test_requires_login(session_factory: sessionmaker[Session], validator: Validator) -> None:
    anonymous = Api(session_factory, key_validator=validator)
    assert anonymous.client.get("/api/onboarding/llm-key").status_code == 401


def test_status_before_any_key(api: Api) -> None:
    body = api.client.get("/api/onboarding/llm-key").json()
    assert body["configured"] is False and body["validated_at"] is None


def test_valid_key_is_checked_then_stored_sealed(api: Api, validator: Validator) -> None:
    r = api.client.post("/api/onboarding/llm-key", json={"app_key": "  AIza-good \n"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True and body["has_backfill_key"] is False
    assert "AIza" not in r.text  # never echoed back
    assert validator.seen == ["AIza-good"]
    assert keys_for_account(api.sf, 1) == AccountKeys(app="AIza-good", backfill=None)


def test_invalid_key_is_explained_and_not_stored(api: Api) -> None:
    r = api.client.post("/api/onboarding/llm-key", json={"app_key": "bad-key"})
    assert r.status_code == 422 and r.json()["detail"] == BAD
    assert keys_for_account(api.sf, 1) is None


def test_second_key_is_optional_and_validated(api: Api) -> None:
    ok = api.client.post("/api/onboarding/llm-key", json={"app_key": "good", "backfill_key": "good2"})
    assert ok.json()["has_backfill_key"] is True
    bad = api.client.post("/api/onboarding/llm-key", json={"app_key": "good", "backfill_key": "bad2"})
    assert bad.status_code == 422 and "segunda chave" in bad.json()["detail"]
    assert keys_for_account(api.sf, 1) == AccountKeys(app="good", backfill="good2")  # unchanged


def test_same_key_twice_is_one_key(api: Api) -> None:
    r = api.client.post("/api/onboarding/llm-key", json={"app_key": "good", "backfill_key": "good"})
    assert r.json()["has_backfill_key"] is False


def test_empty_key_is_refused(api: Api) -> None:
    assert api.client.post("/api/onboarding/llm-key", json={"app_key": ""}).status_code == 422
    assert api.client.post("/api/onboarding/llm-key", json={"app_key": "  "}).status_code == 422


def test_the_worker_side_sees_the_new_key_at_once_in_this_process(api: Api) -> None:
    api.client.post("/api/onboarding/llm-key", json={"app_key": "first"})
    api.client.post("/api/onboarding/llm-key", json={"app_key": "second"})
    assert api.deps.llm_keys.keys(1).app == "second"


def test_the_step_is_registered_for_the_onboarding() -> None:
    assert ONBOARDING_STEPS.index("llm_key") == ONBOARDING_STEPS.index("password_optional") + 1


def test_a_working_key_moves_the_onboarding_on(
    api: Api, session_factory: sessionmaker[Session]
) -> None:
    with session_factory.begin() as s:
        s.get_one(User, 1).onboarding_step = "llm_key"
    body = api.client.post("/api/onboarding/llm-key", json={"app_key": "good"}).json()
    assert body["next_step"] == "finish"
    with session_factory() as s:
        assert s.get_one(User, 1).onboarding_step == "finish"


def test_a_rejected_key_keeps_the_user_on_the_step(
    api: Api, session_factory: sessionmaker[Session]
) -> None:
    with session_factory.begin() as s:
        s.get_one(User, 1).onboarding_step = "llm_key"
    assert api.client.post("/api/onboarding/llm-key", json={"app_key": "bad-1"}).status_code == 422
    with session_factory() as s:
        assert s.get_one(User, 1).onboarding_step == "llm_key"
