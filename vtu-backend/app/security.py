"""Password hashing (bcrypt) and access tokens (HS256 JWT, subject = login email).

A token only says who the caller is. Role, account status and associations are re-read from user_account on every
request (app/deps.py), so a disabled account or a logged-out token stops working immediately.
"""
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

ALGORITHM = "HS256"
REQUIRED_CLAIMS = ["sub", "iat", "exp", "jti"]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:          # malformed stored hash, or a password bcrypt refuses: never a match
        return False


def create_token(email: str, role: str) -> tuple[str, int]:
    """Return (token, lifetime in seconds). `role` is informational for the client; the server never trusts it."""
    lifetime = settings.access_token_minutes * 60
    now = datetime.now(timezone.utc)
    claims = {"sub": email, "role": role, "iat": now, "exp": now + timedelta(seconds=lifetime),
              "jti": uuid.uuid4().hex}
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM), lifetime


def decode_token(token: str) -> dict:
    """Verify signature, algorithm and expiry, and require the claims the API relies on. Raises jwt.PyJWTError."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": REQUIRED_CLAIMS})
