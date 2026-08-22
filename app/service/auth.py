"""Small, dependency-free account and session primitives."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.data.models import UserAccount, UserSession

PASSWORD_ITERATIONS = 310_000
SESSION_COOKIE = "healthflow_session"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def valid_email(email: str) -> bool:
    return len(email) <= 254 and bool(EMAIL_RE.fullmatch(email))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    encode = lambda value: base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${encode(salt)}${encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_text, digest_text = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        padding = lambda value: value + "=" * (-len(value) % 4)
        salt = base64.urlsafe_b64decode(padding(salt_text))
        expected = base64.urlsafe_b64decode(padding(digest_text))
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeError):
        return False


def new_account(email: str, password: str, display_name: str | None) -> UserAccount:
    return UserAccount(
        id=str(uuid.uuid4()),
        email=normalize_email(email),
        password_hash=hash_password(password),
        display_name=(display_name or "").strip()[:128] or "健康用户",
    )


def issue_session(account_id: str, *, days: int = 30) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    session = UserSession(
        account_id=account_id,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=days),
    )
    return token, session


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def account_for_request(request, db: Session) -> UserAccount | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    session = (
        db.query(UserSession)
        .filter(
            UserSession.token_hash == session_hash(token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(),
        )
        .first()
    )
    if session is None:
        return None
    account = db.get(UserAccount, session.account_id)
    if account is None or not account.is_active:
        return None
    session.last_seen_at = datetime.now()
    request.state.account_id = account.id
    return account
