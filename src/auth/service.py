"""Login (password -> TOTP), refresh families, lockouts and sessions.

- Lock checks run *before* the password is verified, so a blocked IP learns
  nothing even with the right password.
- 3 failures (password or second factor) from one IP within 24 h block that
  IP until a manual unlock (IP blocking is global). Failures against one
  user from 3+ distinct IPs within 24 h lock *that user* until a manual
  unlock; other users keep logging in. Failures before an unlock no longer count.
- Refresh tokens rotate on every use; presenting an already-rotated token
  (outside a short grace for two tabs racing) revokes the whole family.
"""
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

import pyotp
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.auth.config import AuthSettings
from src.auth.crypto import (
    DUMMY_HASH,
    decrypt,
    encrypt,
    hash_password,
    issue_access,
    new_token,
    token_hash,
    verify_password,
)
from src.auth.enroll import (
    ISSUER,
    PASSWORD_MIN,
    RECOVERY_CODES,
    new_recovery_code,
    normalize_recovery,
    totp_matches,
)
from src.auth.security_log import SecurityLog
from src.db.models import (
    AuthSession,
    IpBlock,
    LoginFailure,
    RecoveryCode,
    SecurityEvent,
    User,
)
from src.llm.types import Schema

FAILURE_WINDOW = timedelta(hours=24)
IP_FAILURES_TO_BLOCK = 3
IPS_TO_LOCK_USER = 3
REFRESH_REUSE_GRACE = timedelta(seconds=10)

Openness = Literal["ok", "ip_blocked"]


class LoginStep(Schema):
    ok: bool
    user_id: int | None
    blocked: bool


class NewTotp(Schema):
    """What the user needs to enrol the authenticator again (shown once)."""

    otpauth_uri: str
    secret: str
    recovery_codes: tuple[str, ...]


PasswordResult = Literal["ok", "wrong_password", "weak"]


class IssuedSession(Schema):
    access: str
    refresh: str
    csrf: str
    family_id: str


class SessionRow(Schema):
    family_id: str
    started_at: datetime
    last_used_at: datetime
    expires_at: datetime
    ip: str | None
    user_agent: str | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class AuthService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: AuthSettings,
        log: SecurityLog,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.sf = session_factory
        self.settings = settings
        self.log = log
        self._now = clock

    # --- lockouts -------------------------------------------------------------

    def check_open(self, ip: str) -> Openness:
        with self.sf() as s:
            block = s.get(IpBlock, ip)
            if block is not None and block.unblocked_at is None:
                return "ip_blocked"
        return "ok"

    def user_locked(self, user_id: int) -> bool:
        with self.sf() as s:
            user = s.get(User, user_id)
            return user is not None and user.locked_at is not None

    def _fail(
        self, ip: str, stage: Literal["password", "totp"], detail: str, user_id: int | None = None
    ) -> None:
        now = self._now()
        since = now - FAILURE_WINDOW
        with self.sf.begin() as s:
            s.add(LoginFailure(ip=ip, stage=stage, created_at=now, user_id=user_id))
            s.flush()
            block = s.get(IpBlock, ip)
            ip_since = max(since, _aware(block.unblocked_at)) if block and block.unblocked_at else since
            ip_failures = s.scalar(
                select(func.count()).select_from(LoginFailure)
                .where(LoginFailure.ip == ip, LoginFailure.created_at > ip_since)
            ) or 0
            block_ip = ip_failures >= IP_FAILURES_TO_BLOCK and (block is None or block.unblocked_at is not None)
            if block_ip:
                if block is None:
                    s.add(IpBlock(ip=ip, reason=f"{ip_failures} falhas em 24h", created_at=now))
                else:
                    block.reason, block.created_at, block.unblocked_at = (
                        f"{ip_failures} falhas em 24h", now, None,
                    )
            lock_user = False
            distinct_ips = 0
            target = s.get(User, user_id) if user_id is not None else None
            if target is not None and target.locked_at is None:
                u_since = (
                    max(since, _aware(target.lock_cleared_at)) if target.lock_cleared_at else since
                )
                distinct_ips = s.scalar(
                    select(func.count(func.distinct(LoginFailure.ip)))
                    .where(LoginFailure.user_id == target.id, LoginFailure.created_at > u_since)
                ) or 0
                lock_user = distinct_ips >= IPS_TO_LOCK_USER
                if lock_user:
                    target.locked_at = now
        self.log.record("login_fail" if stage == "password" else "totp_fail", ip, detail, user_id)
        if block_ip:
            self.log.record("ip_blocked", ip, f"{ip_failures} falhas de login em 24h")
        if lock_user:
            self.log.record("user_lock", ip, f"usuário {user_id} fechado: falhas de {distinct_ips} IPs em 24h")

    def unlock_ip(self, ip: str) -> bool:
        with self.sf.begin() as s:
            block = s.get(IpBlock, ip)
            if block is None or block.unblocked_at is not None:
                return False
            block.unblocked_at = self._now()
        self.log.record("unlock", ip, "desbloqueio manual do IP")
        return True

    def unlock_user(self, user_id: int) -> bool:
        now = self._now()
        with self.sf.begin() as s:
            user = s.get(User, user_id)
            if user is None or user.locked_at is None:
                return False
            user.locked_at = None
            user.lock_cleared_at = now
        self.log.record("unlock", None, f"desbloqueio manual do usuário {user_id}")
        return True

    def locked_users(self) -> list[tuple[int, str, datetime]]:
        with self.sf() as s:
            rows = s.scalars(select(User).where(User.locked_at.is_not(None)).order_by(User.id))
            return [(u.id, u.email or u.username, _aware(u.locked_at)) for u in rows if u.locked_at]

    def blocks(self) -> list[tuple[str, str, datetime]]:
        with self.sf() as s:
            rows = s.scalars(select(IpBlock).where(IpBlock.unblocked_at.is_(None)))
            return [(b.ip, b.reason, b.created_at) for b in rows]

    # --- login ----------------------------------------------------------------

    def find_user(self, login: str) -> int | None:
        """A user by username or (case-insensitive) e-mail."""
        with self.sf() as s:
            user = s.scalars(select(User).where(User.username == login)).first() or s.scalars(
                select(User).where(User.email == login.strip().lower())
            ).first()
            return user.id if user else None

    def password_step(self, username: str, password: str, ip: str) -> LoginStep:
        if self.check_open(ip) != "ok":
            return LoginStep(ok=False, user_id=None, blocked=True)
        user_id = self.find_user(username)
        with self.sf() as s:
            user = s.get(User, user_id) if user_id is not None else None
            if user is not None and user.locked_at is not None:
                return LoginStep(ok=False, user_id=None, blocked=True)
            # Password login needs a password *and* an enrolled second factor:
            # a password alone never starts a session or an enrollment.
            usable = (
                user is not None and user.password_hash is not None
                and user.totp_secret_enc is not None
            )
            stored = user.password_hash if user is not None and user.password_hash else DUMMY_HASH
        if not usable or not verify_password(stored, password):
            self._fail(ip, "password", f"usuário {username[:64]!r}", user_id)
            return LoginStep(ok=False, user_id=None, blocked=self.check_open(ip) != "ok")
        return LoginStep(ok=True, user_id=user_id, blocked=False)

    def google_step(self, user_id: int) -> LoginStep:
        """Google proved the identity; the second factor is still to come.

        The callback does not see the client IP: the IP block is enforced by
        the second-factor step that follows."""
        if self.user_locked(user_id):
            return LoginStep(ok=False, user_id=None, blocked=True)
        return LoginStep(ok=True, user_id=user_id, blocked=False)

    def _second_factor_ok(self, s: Session, user: User, code: str) -> bool:
        digits = code.strip().replace(" ", "")
        if digits.isdigit() and len(digits) == 6:
            return totp_matches(user, self.settings, digits, self._now())
        rc = s.scalars(
            select(RecoveryCode).where(
                RecoveryCode.user_id == user.id,
                RecoveryCode.code_hash == token_hash(normalize_recovery(code)),
                RecoveryCode.used_at.is_(None),
            )
        ).first()
        if rc is None:
            return False
        rc.used_at = self._now()
        return True

    def second_step(self, user_id: int, code: str, ip: str, user_agent: str) -> IssuedSession | None:
        if self.check_open(ip) != "ok" or self.user_locked(user_id):
            return None
        with self.sf.begin() as s:
            user = s.get(User, user_id)
            ok = (
                user is not None and user.totp_secret_enc is not None
                and self._second_factor_ok(s, user, code)
            )
        if not ok:
            self._fail(ip, "totp", "segundo fator inválido", user_id)
            return None
        return self.start_session(user_id, ip, user_agent)

    def start_session(self, user_id: int, ip: str, user_agent: str) -> IssuedSession:
        """A session after the second factor (or right after enrolling it)."""
        with self.sf() as s:
            seen = s.scalar(
                select(func.count()).select_from(SecurityEvent)
                .where(SecurityEvent.kind == "login_ok", SecurityEvent.ip == ip)
            ) or 0
        issued = self._new_family(user_id, ip, user_agent)
        self.log.record("login_ok", ip, user_agent[:200], user_id)
        if not seen:
            self.log.record(
                "login_new_ip", ip, f"primeiro login deste IP ({user_agent[:200]})", user_id
            )
        return issued

    # --- sessions ---------------------------------------------------------------

    def _store_token(
        self, s: Session, user_id: int, family_id: str, ip: str, user_agent: str
    ) -> str:
        now = self._now()
        raw = new_token()
        s.add(AuthSession(
            user_id=user_id, family_id=family_id, token_hash=token_hash(raw),
            created_at=now, last_used_at=now,
            expires_at=now + timedelta(seconds=self.settings.refresh_ttl_s),
            ip=ip[:64], user_agent=user_agent[:500],
        ))
        return raw

    def _issued(self, user_id: int, family_id: str, refresh: str) -> IssuedSession:
        return IssuedSession(
            access=issue_access(user_id, self.settings, self._now()),
            refresh=refresh, csrf=new_token(), family_id=family_id,
        )

    def _new_family(self, user_id: int, ip: str, user_agent: str) -> IssuedSession:
        family_id = str(uuid.uuid4())
        with self.sf.begin() as s:
            raw = self._store_token(s, user_id, family_id, ip, user_agent)
        return self._issued(user_id, family_id, raw)

    def _revoke(self, s: Session, family_id: str, reason: str) -> int:
        result = s.execute(
            update(AuthSession)
            .where(AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=self._now(), revoke_reason=reason)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    def refresh(self, raw: str, ip: str, user_agent: str) -> IssuedSession | None:
        now = self._now()
        reused = False
        with self.sf.begin() as s:
            row = s.scalars(
                select(AuthSession).where(AuthSession.token_hash == token_hash(raw))
            ).first()
            if row is None or row.revoked_at is not None or _aware(row.expires_at) <= now:
                return None
            if row.rotated_at is not None:
                if now - _aware(row.rotated_at) <= REFRESH_REUSE_GRACE:
                    return None  # two tabs refreshed at once; the other one won
                self._revoke(s, row.family_id, "reuse")
                reused = True
                family_id, owner = row.family_id, row.user_id
            else:
                row.rotated_at = now
                row.last_used_at = now
                user_id, family_id = row.user_id, row.family_id
                new_raw = self._store_token(s, user_id, family_id, ip, user_agent)
        if reused:
            self.log.record(
                "refresh_reuse", ip, f"família {family_id} revogada ({user_agent[:200]})", owner
            )
            return None
        return self._issued(user_id, family_id, new_raw)

    def logout(self, raw: str) -> None:
        with self.sf.begin() as s:
            row = s.scalars(
                select(AuthSession).where(AuthSession.token_hash == token_hash(raw))
            ).first()
            if row is None:
                return
            self._revoke(s, row.family_id, "logout")
            ip, owner = row.ip, row.user_id
        self.log.record("logout", ip, "", owner)

    def family_of(self, raw: str) -> str | None:
        with self.sf() as s:
            row = s.scalars(
                select(AuthSession).where(AuthSession.token_hash == token_hash(raw))
            ).first()
            return row.family_id if row else None

    def sessions(self, user_id: int) -> list[SessionRow]:
        now = self._now()
        with self.sf() as s:
            live = s.scalars(
                select(AuthSession).where(
                    AuthSession.user_id == user_id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.rotated_at.is_(None),
                    AuthSession.expires_at > now,
                ).order_by(AuthSession.last_used_at.desc())
            ).all()
            starts: dict[str, datetime] = {
                family: started
                for family, started in s.execute(
                    select(AuthSession.family_id, func.min(AuthSession.created_at))
                    .where(AuthSession.family_id.in_([r.family_id for r in live]))
                    .group_by(AuthSession.family_id)
                )
            }
            return [
                SessionRow(
                    family_id=r.family_id, started_at=starts.get(r.family_id, r.created_at),
                    last_used_at=r.last_used_at, expires_at=r.expires_at,
                    ip=r.ip, user_agent=r.user_agent,
                )
                for r in live
            ]

    def revoke_family(self, user_id: int, family_id: str) -> bool:
        with self.sf.begin() as s:
            owned = s.scalars(
                select(AuthSession.id).where(
                    AuthSession.family_id == family_id, AuthSession.user_id == user_id
                )
            ).first()
            if owned is None or not self._revoke(s, family_id, "user"):
                return False
        self.log.record("session_revoked", None, f"família {family_id}", user_id)
        return True

    # --- account management (the user's own credentials) -----------------------

    def revoke_others(self, user_id: int, keep_family: str | None) -> int:
        """End every session of the user except the one making the request."""
        with self.sf.begin() as s:
            families = s.scalars(
                select(AuthSession.family_id).where(
                    AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
                ).distinct()
            ).all()
            ended = sum(self._revoke(s, f, "user") > 0 for f in families if f != keep_family)
        if ended:
            self.log.record("sessions_revoked", None, f"{ended} sessão(ões) encerrada(s)", user_id)
        return ended

    def change_password(
        self, user_id: int, current: str, new: str, keep_family: str | None
    ) -> PasswordResult:
        if len(new) < PASSWORD_MIN:
            return "weak"
        with self.sf.begin() as s:
            user = s.get(User, user_id)
            if user is None:
                return "wrong_password"
            # a Google-only user has no password yet: nothing to confirm
            if user.password_hash and not verify_password(user.password_hash, current):
                return "wrong_password"
            user.password_hash = hash_password(new)
            user.password_changed_at = self._now()
        self.log.record("password_changed", None, "senha trocada", user_id)
        self.revoke_others(user_id, keep_family)
        return "ok"

    def rotate_totp(
        self, user_id: int, password: str, code: str, keep_family: str | None
    ) -> NewTotp | None:
        """New authenticator secret + recovery codes, after password and a live code."""
        secret = pyotp.random_base32()
        codes = tuple(new_recovery_code() for _ in range(RECOVERY_CODES))
        with self.sf.begin() as s:
            user = s.get(User, user_id)
            if user is None or not user.totp_secret_enc:
                return None
            if user.password_hash and not verify_password(user.password_hash, password):
                return None
            digits = code.strip().replace(" ", "")
            current = decrypt(user.totp_secret_enc, self.settings.app_secret)
            if not (digits.isdigit() and len(digits) == 6
                    and pyotp.TOTP(current).verify(digits, for_time=self._now(), valid_window=1)):
                return None
            user.totp_secret_enc = encrypt(secret, self.settings.app_secret)
            s.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))
            s.add_all(
                RecoveryCode(user_id=user_id, code_hash=token_hash(normalize_recovery(c)))
                for c in codes
            )
            label = user.email or user.username
        self.log.record("totp_changed", None, "autenticador trocado", user_id)
        self.revoke_others(user_id, keep_family)
        uri = pyotp.TOTP(secret).provisioning_uri(name=label, issuer_name=ISSUER)
        return NewTotp(otpauth_uri=uri, secret=secret, recovery_codes=codes)

    def reset_totp(self, user_id: int, by_admin: int) -> bool:
        """Admin: drop the user's second factor and sessions; they enrol again at next login."""
        with self.sf.begin() as s:
            user = s.get(User, user_id)
            if user is None:
                return False
            user.totp_secret_enc = None  # next Google login re-enrolls (begin_enrollment_if_needed)
            user.onboarding_step = "totp"
            s.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))
            s.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=self._now(), revoke_reason="totp_reset")
            )
        self.log.record("totp_reset", None, f"2FA resetado pelo administrador #{by_admin}", user_id)
        return True

    def confirm_identity(self, user_id: int, password: str | None, code: str | None, ip: str) -> bool:
        """Re-ask for the password or a second-factor code before something
        irreversible. A wrong answer counts as a login failure (same lockouts)."""
        if self.check_open(ip) != "ok" or not (password or code):
            return False
        with self.sf.begin() as s:
            user = s.get(User, user_id)
            if user is None:
                ok = False
            elif password:  # a Google-only user has no password: only the code confirms
                ok = user.password_hash is not None and verify_password(user.password_hash, password)
            else:
                ok = self._second_factor_ok(s, user, code or "")
        if not ok:
            self._fail(ip, "password" if password else "totp", "reconfirmação de identidade falhou",
                       user_id)
        return ok
