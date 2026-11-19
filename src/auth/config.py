"""Auth/HTTP security settings from the environment (read once at start-up)."""
import os
from ipaddress import IPv4Network, IPv6Network, ip_network

from src.llm.types import Schema

DEFAULT_TRUSTED_PROXIES = "127.0.0.1/32,172.16.0.0/12"  # host nginx via the docker bridge
JWT_SECRET_MIN = 32


class AuthSettings(Schema):
    jwt_secret: str
    app_secret: str  # Fernet key: encrypts the TOTP secret at rest
    origin: str  # the panel's origin, e.g. https://example.org
    production: bool
    trusted_proxies: tuple[IPv4Network | IPv6Network, ...]
    access_ttl_s: int = 600
    refresh_ttl_s: int = 14 * 86400
    mfa_ttl_s: int = 300
    download_ttl_s: int = 60


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} não definida (veja .env.example)")
    return value


def load_auth_settings() -> AuthSettings:
    jwt_secret = _required("JWT_SECRET")
    if len(jwt_secret) < JWT_SECRET_MIN:
        raise RuntimeError(f"JWT_SECRET precisa de pelo menos {JWT_SECRET_MIN} caracteres")
    proxies = os.getenv("TRUSTED_PROXIES", DEFAULT_TRUSTED_PROXIES)
    return AuthSettings(
        jwt_secret=jwt_secret,
        app_secret=_required("APP_SECRET"),
        origin=_required("APP_ORIGIN").rstrip("/"),
        production=os.getenv("APP_ENV", "") == "production",
        trusted_proxies=tuple(ip_network(p.strip()) for p in proxies.split(",") if p.strip()),
    )
