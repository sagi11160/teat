"""Password hashing, session tokens and login rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuthSession, LoginAttempt, User, utcnow

_hasher = PasswordHasher()
_secret: bytes | None = None

LOGIN_WINDOW = timedelta(minutes=10)
LOGIN_MAX_FAILURES = 8


def configure_secret(secret_key: str, data_dir: Path) -> None:
    """Uses MB_SECRET_KEY, or a per-installation key persisted under the data dir."""
    global _secret
    if secret_key:
        _secret = secret_key.encode()
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    key_file = data_dir / ".secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(48))
        key_file.chmod(0o600)
    _secret = key_file.read_text().strip().encode()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def _token_digest(token: str) -> str:
    if _secret is None:
        raise RuntimeError("security.configure_secret() was not called")
    return hmac.new(_secret, token.encode(), hashlib.sha256).hexdigest()


def new_invite_token() -> str:
    return secrets.token_urlsafe(18)


def create_session(db: Session, user: User, ttl_hours: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            token_hash=_token_digest(token),
            user_id=user.id,
            expires_at=utcnow() + timedelta(hours=ttl_hours),
        )
    )
    db.flush()
    return token


def resolve_session(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_digest(token)))
    if sess is None or sess.revoked_at is not None:
        return None
    if _aware(sess.expires_at) <= utcnow():
        return None
    return sess.user


def revoke_session(db: Session, token: str | None) -> None:
    if not token:
        return
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_digest(token)))
    if sess is not None and sess.revoked_at is None:
        sess.revoked_at = utcnow()


def login_allowed(db: Session, key: str) -> bool:
    row = db.get(LoginAttempt, key)
    if row is None:
        return True
    if utcnow() - _aware(row.window_start) > LOGIN_WINDOW:
        return True
    return row.count < LOGIN_MAX_FAILURES


def record_login_failure(db: Session, key: str) -> None:
    row = db.get(LoginAttempt, key)
    now = utcnow()
    if row is None:
        db.add(LoginAttempt(key=key, count=1, window_start=now))
    elif now - _aware(row.window_start) > LOGIN_WINDOW:
        row.count = 1
        row.window_start = now
    else:
        row.count += 1


def clear_login_failures(db: Session, key: str) -> None:
    row = db.get(LoginAttempt, key)
    if row is not None:
        db.delete(row)


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes even for timezone-aware columns.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
