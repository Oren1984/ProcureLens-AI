"""Authentication: Argon2id passwords, server-side sessions, throttling, expiry, logout.

Session tokens are 256-bit values from ``secrets``; only their SHA-256 digest is stored.
The raw token lives only in the server-side Streamlit session state of one browser tab.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from procurelens.adapters.persistence.db import Database
from procurelens.adapters.persistence.models import AuthSession, LoginAttempt, User
from procurelens.security.passwords import (
    generate_password,
    hash_password,
    needs_rehash,
    verify_password,
)
from procurelens.security.permissions import AuthenticationError, Principal, Role

from .audit import record_audit

log = logging.getLogger(__name__)
GENERIC_LOGIN_ERROR = "Sign-in failed. Check your username and password, or try again later."
LAST_SEEN_WRITE_INTERVAL = timedelta(seconds=30)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def utcnow() -> datetime:
    return datetime.now(UTC)


class AuthService:
    def __init__(
        self,
        db: Database,
        *,
        idle_minutes: int = 30,
        absolute_hours: int = 8,
        max_failures: int = 5,
        window_minutes: int = 15,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.db = db
        self.idle = timedelta(minutes=idle_minutes)
        self.absolute = timedelta(hours=absolute_hours)
        self.max_failures = max_failures
        self.window = timedelta(minutes=window_minutes)
        self.clock = clock

    # -- login / logout -------------------------------------------------------------------
    def login(self, username: str, password: str) -> tuple[str, Principal]:
        username = (username or "").strip().lower()[:64]
        now = self.clock()
        with self.db.transaction() as s:
            recent_failures = s.scalar(
                select(func.count())
                .select_from(LoginAttempt)
                .where(
                    LoginAttempt.username == username,
                    LoginAttempt.success.is_(False),
                    LoginAttempt.attempted_at > now - self.window,
                )
            )
            throttled = (recent_failures or 0) >= self.max_failures
            user = s.scalar(select(User).where(User.username == username))
            # Always run a hash verification so timing does not reveal valid usernames.
            ok = verify_password(user.password_hash if user else None, password or "")
            ok = ok and user is not None and user.is_active and not throttled
            s.add(LoginAttempt(username=username, attempted_at=now, success=ok))
            if not ok:
                log.info("login failed", extra={"event": "login_failed", "throttled": throttled})
                # Commit the failed attempt, then signal failure.
                s.commit()
                raise AuthenticationError(GENERIC_LOGIN_ERROR)
            if needs_rehash(user.password_hash):
                user.password_hash = hash_password(password)
            token = secrets.token_urlsafe(32)
            sess = AuthSession(
                user_id=user.id,
                token_hash=_digest(token),
                created_at=now,
                last_seen_at=now,
                absolute_expires_at=now + self.absolute,
            )
            s.add(sess)
            s.flush()
            record_audit(
                s, actor=user.username, action="login", entity_type="user", entity_id=user.id
            )
            principal = Principal(user.id, user.username, Role(user.role), sess.id)
        log.info("login succeeded", extra={"event": "login", "user": principal.username})
        return token, principal

    def validate(self, token: str | None) -> Principal:
        """Re-validate the session on every request; raises AuthenticationError."""
        if not token:
            raise AuthenticationError("Not signed in.")
        now = self.clock()
        with self.db.transaction() as s:
            sess = s.scalar(select(AuthSession).where(AuthSession.token_hash == _digest(token)))
            if (
                sess is None
                or sess.revoked_at is not None
                or now >= sess.absolute_expires_at
                or now - sess.last_seen_at >= self.idle
                or not sess.user.is_active
            ):
                if sess is not None and sess.revoked_at is None:
                    sess.revoked_at = now
                raise_after = True
            else:
                raise_after = False
                if now - sess.last_seen_at >= LAST_SEEN_WRITE_INTERVAL:
                    sess.last_seen_at = now
                principal = Principal(
                    sess.user.id, sess.user.username, Role(sess.user.role), sess.id
                )
        if raise_after:
            raise AuthenticationError("Your session has expired. Please sign in again.")
        return principal

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self.db.transaction() as s:
            sess = s.scalar(select(AuthSession).where(AuthSession.token_hash == _digest(token)))
            if sess is not None and sess.revoked_at is None:
                sess.revoked_at = self.clock()
                record_audit(
                    s,
                    actor=sess.user.username,
                    action="logout",
                    entity_type="user",
                    entity_id=sess.user_id,
                )

    # -- administration (CLI only) ----------------------------------------------------------
    def create_user(self, username: str, role: Role, password: str, *, actor: str) -> int:
        with self.db.transaction() as s:
            user = User(
                username=username.lower(), role=role.value, password_hash=hash_password(password)
            )
            s.add(user)
            s.flush()
            record_audit(
                s,
                actor=actor,
                action="user_created",
                entity_type="user",
                entity_id=user.id,
                details={"username": user.username, "role": role},
            )
            return user.id

    def reset_password(self, username: str, *, actor: str) -> str:
        """Set a new generated password and revoke all of the user's sessions."""
        password = generate_password()
        now = self.clock()
        with self.db.transaction() as s:
            user = s.scalar(select(User).where(User.username == username.lower()))
            if user is None:
                raise LookupError(f"No such user: {username}")
            user.password_hash = hash_password(password)
            user.password_changed_at = now
            s.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            s.execute(LoginAttempt.__table__.delete().where(LoginAttempt.username == user.username))
            record_audit(
                s,
                actor=actor,
                action="password_reset",
                entity_type="user",
                entity_id=user.id,
                reason="Password reset via CLI",
            )
        return password
