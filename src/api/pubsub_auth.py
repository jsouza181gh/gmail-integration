"""Verifies the OIDC token Pub/Sub attaches to authenticated push requests.

Configure the push subscription with "Enable authentication", a service
account and an audience; then set PUBSUB_SERVICE_ACCOUNT and PUBSUB_AUDIENCE.
"""
from collections.abc import Callable, Mapping

from pydantic import BaseModel, ValidationError

type Verify = Callable[[str, str], Mapping[str, object]]


class _Claims(BaseModel):
    email: str
    email_verified: bool


def _google_verify(token: str, audience: str) -> Mapping[str, object]:
    from google.auth.transport import requests
    from google.oauth2 import id_token

    claims: Mapping[str, object] = id_token.verify_oauth2_token(
        token, requests.Request(), audience=audience
    )
    return claims


class PubSubVerifier:
    def __init__(self, audience: str, service_account: str, verify: Verify = _google_verify) -> None:
        self.audience = audience
        self.service_account = service_account
        self._verify = verify

    def ok(self, authorization: str | None) -> bool:
        if not authorization or not authorization.startswith("Bearer "):
            return False
        try:
            claims = _Claims.model_validate(self._verify(authorization[7:], self.audience))
        except (ValueError, ValidationError):  # bad signature, audience, expiry, issuer
            return False
        return claims.email_verified and claims.email == self.service_account
