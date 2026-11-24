"""A second panel user (own Gmail account, own login) next to the Api fake's first one."""
from datetime import UTC, datetime

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from src.auth.crypto import encrypt, hash_password
from src.db.models import GmailAccount, User
from src.db.scope import system_scope
from tests.api_fakes import SETTINGS, Api

T0 = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
SECRET_B = pyotp.random_base32()


def add_user(
    sf: sessionmaker[Session], user_id: int, username: str, *, role: str = "member",
    with_account: bool = True, password: str = "senha-forte-123",
) -> str:
    """Insert user `user_id` (and its Gmail account when asked); returns its TOTP secret."""
    secret = pyotp.random_base32()
    with system_scope(), sf.begin() as s:
        s.add(User(id=user_id, username=username, email=f"{username}@example.com", role=role,
                   password_hash=hash_password(password),
                   totp_secret_enc=encrypt(secret, SETTINGS.app_secret),
                   created_at=T0, password_changed_at=T0))
        s.flush()
        if with_account:
            s.add(GmailAccount(id=user_id, user_id=user_id, email_address=f"{username}@example.com",
                               status="connected", connected_at=T0))
    return secret


def login(api: Api, username: str, secret: str, password: str = "senha-forte-123") -> TestClient:
    client = api.new_client()
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 204, r.text
    r = client.post("/api/auth/2fa", json={"code": pyotp.TOTP(secret).at(api.clock())})
    assert r.status_code == 204, r.text
    client.headers["X-CSRF-Token"] = client.cookies["csrf"]
    return client


def make_admin(sf: sessionmaker[Session], user_id: int) -> None:
    with system_scope(), sf.begin() as s:
        user = s.get(User, user_id)
        assert user is not None
        user.role = "admin"
