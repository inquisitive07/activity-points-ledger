"""Authentication dependencies. Every authenticated request reloads the account from user_account."""
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AccountStatus, AuditLog, UserAccount
from app.security import decode_token

bearer = HTTPBearer(auto_error=False)
LOGOUT = "LOGOUT"


@dataclass
class Auth:
    user: UserAccount
    claims: dict


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


def token_revoked(db: Session, email: str, jti: str) -> bool:
    """A token is revoked once its holder logged out with it. Logout writes an audit_log row carrying the token's
    jti, so revocation is durable (survives restarts) without a separate token table."""
    return db.scalar(select(exists().where(AuditLog.user_email == email, AuditLog.action == LOGOUT,
                                           AuditLog.new_values["jti"].astext == jti)))


def get_auth(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> Auth:
    if not creds:
        raise unauthorized("Not authenticated")
    try:
        claims = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise unauthorized("Invalid or expired token")
    email, jti = claims.get("sub"), claims.get("jti")
    if not isinstance(email, str) or not isinstance(jti, str):
        raise unauthorized("Invalid or expired token")
    user = db.get(UserAccount, email)
    if user is None or user.account_status != AccountStatus.ACTIVE:
        raise unauthorized("Account disabled or not found")
    if token_revoked(db, email, jti):
        raise unauthorized("Token has been logged out")
    return Auth(user, claims)


def get_current_user(auth: Auth = Depends(get_auth)) -> UserAccount:
    return auth.user
