from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ratelimit
from app.audit import audit
from app.db import get_db
from app.deps import LOGOUT, Auth, get_auth, get_current_user
from app.models import AccountStatus, Club, Department, Role, Student, UserAccount
from app.schemas import LoginIn, TokenOut, UserOut
from app.security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# Checked when the email is unknown, so both cases take the same bcrypt time (no account enumeration by timing).
_DUMMY_HASH = hash_password("timing-equaliser")


def to_out(user: UserAccount, db: Session) -> UserOut:
    out = UserOut(email=user.email, role=user.role, display_name=user.role.title(), student_usn=user.student_usn,
                  department_code=user.department_code, club_id=user.club_id, last_login_at=user.last_login_at)
    if user.role == Role.STUDENT:
        student = db.get(Student, user.student_usn)
        out.display_name, out.department_code = student.name, student.department_code
    elif user.role == Role.HOD:
        out.display_name = f"HOD, {db.get(Department, user.department_code).department_name}"
    elif user.role == Role.CLUB_LEAD:
        out.club_name = db.scalar(select(Club.club_name).where(Club.club_id == user.club_id))
        out.display_name = f"{out.club_name} lead"
    return out


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    key = f"{email}|{request.client.host if request.client else '-'}"
    ratelimit.check(key)
    user = db.get(UserAccount, email)
    password_ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not password_ok:
        ratelimit.record_failure(key)
        # The typed email is recorded only when it names a real account (user_email): an unknown "email" may be a
        # password typed into the wrong field, and must not end up in the audit log.
        audit(db, user.email if user else None, "LOGIN_FAILED", "user_account",
              new_values={"reason": "wrong_password" if user else "unknown_account"}, request=request)
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    if user.account_status != AccountStatus.ACTIVE:
        audit(db, user.email, "LOGIN_REJECTED", "user_account",
              new_values={"reason": "account_disabled"}, request=request)
        db.commit()
        raise HTTPException(403, "This account is disabled. Contact the Dean's office.")
    ratelimit.reset(key)
    old_login = user.last_login_at
    user.last_login_at = datetime.now(timezone.utc)
    token, lifetime = create_token(user.email, user.role)
    audit(db, user.email, "LOGIN", "user_account",
          old_values={"last_login_at": old_login.isoformat() if old_login else None},
          new_values={"last_login_at": user.last_login_at.isoformat()}, request=request)
    db.commit()
    return TokenOut(access_token=token, expires_in=lifetime, user=to_out(user, db))


@router.get("/me", response_model=UserOut)
def me(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return to_out(user, db)


@router.post("/logout", status_code=204)
def logout(request: Request, auth: Auth = Depends(get_auth), db: Session = Depends(get_db)):
    """Revoke the presented token. The LOGOUT audit row (with the token's jti) is what get_auth checks."""
    audit(db, auth.user.email, LOGOUT, "user_account", new_values={"jti": auth.claims["jti"]}, request=request)
    db.commit()
    return Response(status_code=204)
