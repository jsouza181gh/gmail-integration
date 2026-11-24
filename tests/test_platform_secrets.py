import pytest
from cryptography.fernet import Fernet

from src.platform.secrets import seal, unseal


def test_seal_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET", Fernet.generate_key().decode())
    token = seal("refresh-abc")
    assert token != "refresh-abc"
    assert unseal(token) == "refresh-abc"


def test_rotation_first_key_encrypts_older_keys_still_open(monkeypatch: pytest.MonkeyPatch) -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv("APP_SECRET", old)
    sealed_old = seal("x")
    monkeypatch.setenv("APP_SECRET", f"{new},{old}")
    assert unseal(sealed_old) == "x"
    sealed_new = seal("y")
    monkeypatch.setenv("APP_SECRET", new)  # the old key is gone: new tokens still open
    assert unseal(sealed_new) == "y"


def test_invalid_token_is_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET", Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        unseal("not-a-token")
    other = Fernet(Fernet.generate_key()).encrypt(b"z").decode()
    with pytest.raises(ValueError):
        unseal(other)


def test_missing_secret_is_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        seal("x")
