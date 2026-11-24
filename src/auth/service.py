"""Login (password -> TOTP), refresh families, lockouts and sessions.

- Lock checks run *before* the password is verified, so a blocked IP learns
  nothing even with the right password.
- 3 failures (password or second factor) from one IP within 24 h block that
  IP until a manual unlock; failures from 3+ distinct IPs within 24 h close
  the login globally. Failures before an unlock no longer count.
- Refresh tokens rotate on every use; presenting an already-rotated token
  (outside a short grace for two tabs racing) revokes the whole family.
"""
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

import pyotp
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.auth.config import AuthSettings
from src.auth.crypto import (
    DUMMY_HASH,
    decrypt,
    issue_access,
    new_token,
    token_hash,
    verify_password,
)
from src.auth.security_log import SecurityLog
from src.db.models import (
    AppSetting,
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
IPS_TO_LOCK_GLOBALLY = 3
REFRESH_REUSE_GRACE = timedelta(seconds=10)
GLOBAL_LOCK = "global_lock"
GLOBAL_UNLOCKED_AT = "global_unlocked_at"

Openness = Literal["ok", "ip_blocked", "global_lock"]


class LoginStep(Schema):
    ok: bool
    user_id: int | None
    blocked: bool


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


def normalize_recovery(code: str) -> str:
    return re.sub(r"[^a-z0-9]", "", code.lower())


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

    def _setting(self, s: Session, key: str) -> str | None:
        row = s.get(AppSetting, key)
        return row.value if row else None

    def check_open(self, ip: str) -> Openness:
        with self.sf() as s:
            if self._setting(s, GLOBAL_LOCK) == "1":
                return "global_lock"
            block = s.get(IpBlock, ip)
            if block is not None and block.unblocked_at is None:
                return "ip_blocked"
        return "ok"

    def _fail(self, ip: str, stage: Literal["password", "totp"], detail: str) -> None:
        now = self._now()
        since = now - FAILURE_WINDOW
        with self.sf.begin() as s:
            s.add(LoginFailure(ip=ip, stage=stage, created_at=now))
            s.flush()
            block = s.get(IpBlock, ip)
            ip_since = max(since, _aware(block.unblocked_at)) if block and block.unblocked_at else since
            ip_failures = s.scalar(
                select(func.count()).select_from(LoginFailure)
                .where(LoginFailure.ip == ip, LoginFailure.created_at > ip_since)
            ) or 0
            unlocked = self._setting(s, GLOBAL_UNLOCKED_AT)
            g_since = max(since, datetime.fromisoformat(unlocked)) if unlocked else since
            distinct_ips = s.scalar(
                select(func.count(func.distinct(LoginFailure.ip)))
                .where(LoginFailure.created_at > g_since)
            ) or 0
            block_ip = ip_failures >= IP_FAILURES_TO_BLOCK and (block is None or block.unblocked_at is not None)
            if block_ip:
                if block is None:
                    s.add(IpBlock(ip=ip, reason=f"{ip_failures} falhas em 24h", created_at=now))
                else:
                    block.reason, block.created_at, block.unblocked_at = (
                        f"{ip_failures} falhas em 24h", now, None,
                    )
            lock_global = distinct_ips >= IPS_TO_LOCK_GLOBALLY and self._setting(s, GLOBAL_LOCK) != "1"
            if lock_global:
                s.merge(AppSetting(key=GLOBAL_LOCK, value="1", updated_at=now))
        self.log.record("login_fail" if stage == "password" else "totp_fail", ip, detail)
        if block_ip:
            self.log.record("ip_blocked", ip, f"{ip_failures} falhas de login em 24h")
        if lock_global:
            self.log.record("global_lock", ip, f"falhas de {distinct_ips} IPs distintos em 24h")

    def unlock_ip(self, ip: str) -> bool:
        with self.sf.begin() as s:
            block = s.get(IpBlock, ip)
            if block is None or block.unblocked_at is not None:
                return False
            block.unblocked_at = self._now()
        self.log.record("unlock", ip, "desbloqueio manual do IP")
        return True

    def unlock_global(self) -> bool:
        now = self._now()
        with self.sf.begin() as s:
            if self._setting(s, GLOBAL_LOCK) != "1":
                return False
            s.merge(AppSetting(key=GLOBAL_LOCK, value="0", updated_at=now))
            s.merge(AppSetting(key=GLOBAL_UNLOCKED_AT, value=now.isoformat(), updated_at=now))
        self.log.record("unlock", None, "desbloqueio manual do login global")
        return True

    def blocks(self) -> list[tuple[str, str, datetime]]:
        with self.sf() as s:
            rows = s.scalars(select(IpBlock).where(IpBlock.unblocked_at.is_(None)))
            return [(b.ip, b.reason, b.created_at) for b in rows]

    # --- login ----------------------------------------------------------------

    def password_step(self, username: str, password: str, ip: str) -> LoginStep:
        if self.check_open(ip) != "ok":
            return LoginStep(ok=False, user_id=None, blocked=True)
        with self.sf() as s:
            user = s.scalars(select(User).where(User.username == username)).first()
            password_hash = user.password_hash if user else DUMMY_HASH
            user_id = user.id if user else None
        if user_id is None or not verify_password(password_hash, password):
            self._fail(ip, "password", f"usuário {username[:64]!r}")
            return LoginStep(ok=False, user_id=None, blocked=self.check_open(ip) != "ok")
        return LoginStep(ok=True, user_id=user_id, blocked=False)

    def _second_factor_ok(self, s: Session, user: User, code: str) -> bool:
        digits = code.strip().replace(" ", "")
        if digits.isdigit() and len(digits) == 6:
            secret = decrypt(user.totp_secret_enc, self.settings.app_secret)
            return pyotp.TOTP(secret).verify(digits, for_time=self._now(), valid_window=1)
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
        if self.check_open(ip) != "ok":
            return None
        with self.sf.begin() as s:
            user = s.get(User, user_id)
            ok = user is not None and self._second_factor_ok(s, user, code)
        if not ok:
            self._fail(ip, "totp", "segundo fator inválido")
            return None
        with self.sf() as s:
            seen = s.scalar(
                select(func.count()).select_from(SecurityEvent)
                .where(SecurityEvent.kind == "login_ok", SecurityEvent.ip == ip)
            ) or 0
        issued = self._new_family(user_id, ip, user_agent)
        self.log.record("login_ok", ip, user_agent[:200])
        if not seen:
            self.log.record("login_new_ip", ip, f"primeiro login deste IP ({user_agent[:200]})")
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
                family_id = row.family_id
            else:
                row.rotated_at = now
                row.last_used_at = now
                user_id, family_id = row.user_id, row.family_id
                new_raw = self._store_token(s, user_id, family_id, ip, user_agent)
        if reused:
            self.log.record("refresh_reuse", ip, f"família {family_id} revogada ({user_agent[:200]})")
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
            ip = row.ip
        self.log.record("logout", ip, "")

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
        self.log.record("session_revoked", None, f"família {family_id}")
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
            elif password:
                ok = verify_password(user.password_hash, password)
            else:
                ok = self._second_factor_ok(s, user, code or "")
        if not ok:
            self._fail(ip, "password" if password else "totp", "reconfirmação de identidade falhou")
        return ok
