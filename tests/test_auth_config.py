from ipaddress import ip_network

import pytest

from src.auth.config import load_auth_settings

KEY = "x" * 32
FERNET = "2xJ0yNw0b7a1Y8l8Cq0Rk3F2nQm8sV9pQ0rW4tZ6uX8="


def env(monkeypatch: pytest.MonkeyPatch, **kw: str) -> None:
    for k in ("JWT_SECRET", "APP_SECRET", "APP_ORIGIN", "APP_ENV", "TRUSTED_PROXIES"):
        monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


def test_load_auth_settings_requires_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    env(monkeypatch, APP_SECRET=FERNET, APP_ORIGIN="https://x.org")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        load_auth_settings()
    env(monkeypatch, JWT_SECRET="short", APP_SECRET=FERNET, APP_ORIGIN="https://x.org")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        load_auth_settings()


def test_trusted_proxies_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    env(monkeypatch, JWT_SECRET=KEY, APP_SECRET=FERNET, APP_ORIGIN="https://x.org/",
        APP_ENV="production")
    s = load_auth_settings()
    assert s.trusted_proxies == (ip_network("127.0.0.1/32"), ip_network("172.16.0.0/12"))
    assert s.production and s.origin == "https://x.org" and s.access_ttl_s == 600
    env(monkeypatch, JWT_SECRET=KEY, APP_SECRET=FERNET, APP_ORIGIN="https://x.org",
        TRUSTED_PROXIES="10.0.0.0/8")
    assert load_auth_settings().trusted_proxies == (ip_network("10.0.0.0/8"),)
